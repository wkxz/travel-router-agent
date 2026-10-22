"""
History handler — EdgeOne Makers Python cloud function.

POST /history
  Body:    { "conversation_id": "<uuid>" }
  Returns: { "conversation_id": "<uuid>", "messages": Message[] }

  Message shape (matches src/types.ts):
    { "id": str, "role": "user" | "assistant", "content": str, "timestamp": int }

How history is stored
─────────────────────
The chat handler (`agents/chat/index.py`) wires the OpenAI Agents SDK Runner
to an EdgeOne session via `context.store.openai_session(conversation_id)`.

For each turn the SDK appends one `Message` per "input item" — user message,
tool call, tool output, assistant message — with:

  Message.role       : str          # 'user' | 'assistant' | 'tool'
  Message.content    : dict         # full Agents SDK input item:
                                    #   { type, role, content: [{type:"input_text"|"output_text", text}] }
  Message.metadata   : dict         # { agent_sdk_session: True,
                                    #   item_type: "message" | "function_call" | "function_call_output" | ...,
                                    #   run_id: "<turn-uuid>" }
  Message.message_id : str
  Message.created_at : int          # ms timestamp

A single turn typically writes multiple assistant items: an opening narration
("Let me check the weather..."), one or more tool calls and outputs, then the
final answer. They all share the same `run_id`. This handler:

  1. drops everything except role 'user' / 'assistant' messages
     (i.e. drops `function_call`, `function_call_output`, `reasoning`, ...)
  2. sorts the survivors by `created_at`
  3. merges consecutive assistant fragments with the same `run_id` into
     a single Message so the UI renders one bubble per turn
  4. folds adjacent duplicate bubbles, which can happen because chat writes
     a user-index copy for the sidebar and the OpenAI session writes the
     same user input for model memory

Trip plans: the itinerary is streamed to the browser as an SSE `plan` event and
never repeated in the assistant's text, so on reload it would be lost. Step 1
therefore keeps one kind of `function_call_output`: the output of
generate_trip_itinerary, recognised by its `plan.kind == "trip_plan"` marker.
It is attached to the assistant bubble of the same run as `message.plan`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler
from typing import Any

# EdgeOne loads each index.py as a top-level module without package context,
# so the parent directory must be on sys.path to import sibling helpers.
_PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from _logger import create_logger  # noqa: E402

logger = create_logger("history")


def _read_body(rfile, headers) -> dict:
    """Decode the JSON request body; return an empty dict on any failure."""
    length = int(headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    try:
        return json.loads(rfile.read(length).decode("utf-8")) or {}
    except (ValueError, UnicodeDecodeError):
        return {}


def _flatten_content(content: Any) -> str:
    """Extract plain text from an Agents SDK message item's `content` field.

    The SDK nests text in a list of typed parts:
      [{"type": "input_text",  "text": "..."},
       {"type": "output_text", "text": "..."}]
    """
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    if isinstance(content, str):
        return content
    return ""


PLAN_KIND = "trip_plan"


def _extract_plan(output: Any) -> dict | None:
    """Copy of agents/_tools.py::extract_plan — cloud functions deploy separately
    and cannot import the agents package. Keep the two in sync."""
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except ValueError:
            return None
    if not isinstance(output, dict) or output.get("success") is not True:
        return None
    plan = output.get("plan")
    return plan if isinstance(plan, dict) and plan.get("kind") == PLAN_KIND else None


def _to_plan_fragment(record: Any) -> tuple[dict, str | None] | None:
    """A generate_trip_itinerary output → an empty assistant fragment carrying `plan`."""
    metadata = getattr(record, "metadata", None) or {}
    sdk_item = getattr(record, "content", None)
    if not isinstance(sdk_item, dict):
        return None
    if "function_call_output" not in (metadata.get("item_type"), sdk_item.get("type")):
        return None
    output = sdk_item.get("output")
    if isinstance(output, list):
        output = _flatten_content(output)
    plan = _extract_plan(output)
    if not plan:
        return None
    created_at = getattr(record, "created_at", 0) or 0
    message = {
        "id": getattr(record, "message_id", None) or f"plan-{created_at}",
        "role": "assistant",
        "content": "",
        "timestamp": created_at,
        "plan": plan,
    }
    return message, metadata.get("run_id")


def _to_message(record: Any) -> tuple[dict, str | None] | None:
    """Convert a stored `Message` to a frontend `Message` dict + its `run_id`.

    Returns None for records that should not appear in the chat window:
      - non-user/assistant roles (e.g. 'tool')
      - SDK internal items (item_type != 'message')
      - items whose flattened text is empty
    """
    role = getattr(record, "role", None)
    if role not in ("user", "assistant"):
        return None

    metadata = getattr(record, "metadata", None) or {}
    if metadata.get("item_type") not in (None, "message"):
        return None

    sdk_item = getattr(record, "content", None)
    inner = sdk_item.get("content") if isinstance(sdk_item, dict) else sdk_item
    text = _flatten_content(inner)
    if not text:
        return None

    created_at = getattr(record, "created_at", 0) or 0
    message = {
        "id": getattr(record, "message_id", None) or f"{role}-{created_at}",
        "role": role,
        "content": text,
        "timestamp": created_at,
    }
    return message, metadata.get("run_id")


def _merge_assistant_fragments(items: list[tuple[dict, str | None]]) -> list[dict]:
    """Sort by timestamp and merge consecutive assistant fragments per run.

    A single Runner turn can produce multiple assistant message items (a pre-
    tool-call narration plus a final answer, for example). They share a
    `run_id`. Concatenating them with blank lines reconstructs the assistant's
    full reply for the turn so the UI shows one bubble per turn.
    """
    items.sort(key=lambda pair: pair[0]["timestamp"])

    merged: list[dict] = []
    last_run_id: str | None = None
    for msg, run_id in items:
        same_run_assistant = (
            merged
            and run_id is not None
            and run_id == last_run_id
            and merged[-1]["role"] == "assistant"
            and msg["role"] == "assistant"
        )
        if same_run_assistant:
            prev = merged[-1]
            prev["content"] = "\n\n".join(c for c in (prev["content"], msg["content"]) if c)
            if msg.get("plan"):
                prev["plan"] = msg["plan"]
        else:
            merged.append(dict(msg))
            last_run_id = run_id
    return merged


def _dedupe_adjacent(messages: list[dict]) -> list[dict]:
    """Drop adjacent display duplicates after history normalization."""
    deduped: list[dict] = []
    for msg in messages:
        prev = deduped[-1] if deduped else None
        if (
            prev is not None
            and prev.get("role") == msg.get("role")
            and prev.get("content") == msg.get("content")
            and not msg.get("plan")  # 不同轮次的行程即使文字相同也要保留
        ):
            continue
        deduped.append(msg)
    return deduped


class handler(BaseHTTPRequestHandler):
    def _write_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        start = time.time()

        body = _read_body(self.rfile, self.headers)
        conversation_id = str(body.get("conversation_id") or "").strip()

        store = self.context.agent.store
        logger.log(f"get_messages: conversation_id={conversation_id!r}")

        if not conversation_id:
            self._write_json(200, {"conversation_id": conversation_id, "messages": []})
            return

        try:
            history = store.get_messages(conversation_id, limit=100, order="asc") or []

            visible = [
                pair for record in history
                if (pair := _to_message(record) or _to_plan_fragment(record))
            ]
            messages = _dedupe_adjacent(_merge_assistant_fragments(visible))

            elapsed = int((time.time() - start) * 1000)
            logger.log(
                f"get_messages: {len(history)} raw → {len(visible)} visible → "
                f"{len(messages)} bubbles in {elapsed}ms"
            )
            self._write_json(200, {"conversation_id": conversation_id, "messages": messages})

        except Exception as e:
            logger.error(
                f"get_messages failed: conversation_id={conversation_id!r} "
                f"type={type(e).__name__} err={e!r}"
            )
            logger.error(f"traceback:\n{traceback.format_exc()}")
            self._write_json(200, {"conversation_id": conversation_id, "messages": []})
