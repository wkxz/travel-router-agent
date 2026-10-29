"""HTTP 服务：Agent 接口、会话管理与前端静态文件，一个进程全部提供。

启动：python -m server            （或 uvicorn server.app:app --host 0.0.0.0 --port 8000）

接口（均为 POST + JSON，/chat 返回 SSE）：
  /chat                 { message, userId? }，请求头 X-Conversation-Id
  /chat/stop            { conversation_id }
  /conversations        { user_id, limit?, order?, after? }
  /history              { conversation_id }
  /clear-history        { conversation_id }
  /delete-conversation  { conversation_id }
  GET /health
其余 GET 请求返回 dist/ 下的前端（单页应用，找不到的路径回退到 index.html）。
"""

import asyncio
import contextlib
import json
import os
import uuid

from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route

load_dotenv()

from app.agent import chat_events, llm_env  # noqa: E402  先加载 .env，再创建模型客户端
from app._logger import create_logger  # noqa: E402
from .store import Store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_DIR = os.path.join(ROOT, "dist")
DB_PATH = os.getenv("DATABASE_PATH") or os.path.join(ROOT, "data", "travel-agent.db")

logger = create_logger("server")
store = Store(DB_PATH)
# 正在生成的会话 -> 取消信号；/chat/stop 据此中断
_active_runs: dict = {}


def missing_config() -> list:
    """缺失的必填配置项名称。"""
    missing = [f"LLM_{n}" for n in ("API_KEY", "BASE_URL", "MODEL") if not llm_env(n)]
    if not os.getenv("AMAP_API_KEY"):
        missing.append("AMAP_API_KEY")
    return missing


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return body if isinstance(body, dict) else {}


def _str(body: dict, *keys: str) -> str:
    return next((str(body[k]).strip() for k in keys if body.get(k)), "")


def _parse_sse(frame: str) -> tuple:
    event, data = "", None
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
    return event, data


# ---------- Agent ----------

async def chat(request: Request):
    body = await _json(request)
    message = _str(body, "message")
    if not message:
        return JSONResponse({"status": "error", "message": "'message' is required"}, status_code=400)
    cid = (request.headers.get("x-conversation-id") or _str(body, "conversation_id")).strip() or str(uuid.uuid4())
    user_id = _str(body, "userId", "user_id") or None

    store.add_message(cid, "user", message, user_id=user_id, message_id=_str(body, "userMsgId") or None)
    cancel = asyncio.Event()
    _active_runs[cid] = cancel

    async def stream():
        text, plan = [], None
        try:
            async for frame in chat_events(message, cid, store.agent_session(cid), cancel):
                event, data = _parse_sse(frame)
                if event == "text_delta":
                    text.append(data.get("delta", ""))
                elif event == "plan":
                    plan = data
                yield frame
        finally:
            # 停止或断开连接时也保存已生成的部分，刷新后能看到
            if _active_runs.get(cid) is cancel:
                del _active_runs[cid]
            if text or plan:
                store.add_message(cid, "assistant", "".join(text), plan=plan,
                                  message_id=_str(body, "botMsgId") or None)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",  # 关闭 Nginx 缓冲，保证逐字推送
        "X-Conversation-Id": cid,
    })


async def chat_stop(request: Request):
    cid = _str(await _json(request), "conversation_id", "conversationId")
    if not cid:
        return JSONResponse({"status": "error", "message": "conversation_id is required"}, status_code=400)
    cancel = _active_runs.get(cid)
    if cancel:
        cancel.set()
    logger.log(f"[stop] cid={cid} aborted={bool(cancel)}")
    return JSONResponse({"status": "aborting" if cancel else "idle", "conversationId": cid, "aborted": bool(cancel)})


# ---------- 会话管理 ----------

async def conversations(request: Request):
    body = await _json(request)
    user_id = _str(body, "user_id", "userId")
    if not user_id:
        return JSONResponse({"status": "error", "message": "user_id is required"}, status_code=400)
    try:
        limit = max(1, min(100, int(body.get("limit") or 20)))
        offset = max(0, int(body.get("after") or 0))
    except (TypeError, ValueError):
        limit, offset = 20, 0
    items, next_cursor = store.list_conversations(user_id, limit, offset, body.get("order") or "desc")
    return JSONResponse({"conversations": items, "nextCursor": next_cursor, "previousCursor": None})


async def history(request: Request):
    cid = _str(await _json(request), "conversation_id", "conversationId")
    return JSONResponse({"conversation_id": cid, "messages": store.get_messages(cid) if cid else []})


async def clear_history(request: Request):
    cid = _str(await _json(request), "conversation_id", "conversationId")
    if not cid:
        return JSONResponse({"status": "error", "message": "conversation_id is required"}, status_code=400)
    store.clear_messages(cid)
    return JSONResponse({"status": "ok", "conversation_id": cid})


async def delete_conversation(request: Request):
    cid = _str(await _json(request), "conversation_id", "conversationId")
    if not cid:
        return JSONResponse({"status": "error", "message": "conversation_id is required"}, status_code=400)
    store.delete_conversation(cid)
    return JSONResponse({"status": "ok", "conversation_id": cid})


async def health(_request: Request):
    missing = missing_config()
    return JSONResponse({"status": "ok" if not missing else "misconfigured", "missing": missing})


# ---------- 前端 ----------

async def frontend(request: Request):
    if not os.path.isdir(DIST_DIR):
        return PlainTextResponse("前端尚未构建：先运行 npm run build（开发时可用 npx vite 单独启动前端）。", status_code=404)
    path = os.path.normpath(os.path.join(DIST_DIR, request.path_params.get("path", "")))
    if path.startswith(DIST_DIR) and os.path.isfile(path):
        return FileResponse(path)
    return FileResponse(os.path.join(DIST_DIR, "index.html"))


routes = [
    Route("/chat", chat, methods=["POST"]),
    Route("/chat/stop", chat_stop, methods=["POST"]),
    Route("/conversations", conversations, methods=["POST"]),
    Route("/history", history, methods=["POST"]),
    Route("/clear-history", clear_history, methods=["POST"]),
    Route("/delete-conversation", delete_conversation, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
    Route("/{path:path}", frontend, methods=["GET"]),
]


@contextlib.asynccontextmanager
async def lifespan(_app):
    missing = missing_config()
    if missing:
        logger.error(f"缺少配置：{', '.join(missing)}（见 .env.example），相关功能会报错")
    logger.log(f"数据库：{DB_PATH}；前端：{DIST_DIR if os.path.isdir(DIST_DIR) else '未构建'}")
    yield


app = Starlette(routes=routes, lifespan=lifespan)
