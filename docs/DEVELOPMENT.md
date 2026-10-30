# 开发指南

## 1. 环境要求

| 依赖 | 版本（开发时使用） | 说明 |
|---|---|---|
| Python | 3.11 | Agent、排程引擎与 HTTP 服务 |
| Node.js | 22 / 24 | 前端开发与构建 |

```bash
# Python 依赖
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # macOS / Linux 用 .venv/bin/python

# 前端依赖
npm install
```

## 2. 配置密钥

复制 `.env.example` 为 `.env` 并填写（变量说明见 [DEPLOYMENT.md](DEPLOYMENT.md#1-准备密钥)）：

```
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=
AMAP_API_KEY=
```

`.env` 已在 `.gitignore` 中。**密钥只从环境变量读取**：不要硬编码、不要打印、不要提交。
返回给模型或写进日志的错误文本都会先经过 `app/_data/amap.py` 的 `redact_secrets` 脱敏。

## 3. 本地运行

开发时开两个终端：

```bash
# 终端 1：后端（http://127.0.0.1:8000）
./.venv/Scripts/python.exe -m server

# 终端 2：前端热更新（http://localhost:5173，接口请求代理到 8000）
npx vite
```

- 打开 http://localhost:5173 开发前端；接口路径列表在 `vite.config.ts` 的 `proxy` 里，新增接口要同步加上。
  后端不在 8000 时，用环境变量 `BACKEND_URL` 指定。
- 改了 `app/` 或 `server/` 的 Python 代码后重启后端即可（直接运行源码，没有构建步骤）。
- 只想验证生产形态：`npm run build` 后直接打开 http://127.0.0.1:8000 ，由后端托管 `dist/`。
- 本地数据库默认在 `data/travel-agent.db`，删掉即清空所有会话。
- 本机配置了 HTTP 代理时，`curl` 访问本地服务要加 `--noproxy '*'`，否则会被代理拦截返回 502。

快速验证：

```bash
curl --noproxy '*' http://127.0.0.1:8000/health

curl --noproxy '*' -N -X POST http://127.0.0.1:8000/chat \
  -H 'Content-Type: application/json' -H 'X-Conversation-Id: test-1' \
  -d '{"message":"杭州玩 2 天，想去西湖和灵隐寺，想吃片儿川"}'
```

## 4. 测试

```bash
# 单元 / 回归 / 接口测试：不联网、不花钱，几秒内完成
./.venv/Scripts/python.exe -m unittest discover -s tests -t .

# 前端类型检查与生产构建
npx tsc --noEmit -p .
npm run build
```

- 测试用假数据（`tests/fake_amap.py`）覆盖排程、校验、工具层与安全脱敏；
  `tests/test_server.py` 用 Starlette `TestClient` 和假的模型事件流测试全部 HTTP 接口与 SQLite 存储，数据库放在临时目录。
- **修 bug 先写能复现问题的测试**，并确认撤掉修复后该测试会失败，避免写出「怎么都能过」的空测试。
- `tests/test_eval_regressions.py` 里有一条「分天计算 2 秒内完成」的耗时断言，机器繁忙时偶尔会超时，重跑即可。

## 5. 真实环境测评

`eval/` 用 12 条真实提示词在进程内运行与线上相同的 Agent，记录工具调用、行程与回复。
**会产生模型与高德调用费用**，结果写入 `eval/results/`（已忽略，不入库）。

```bash
./.venv/Scripts/python.exe eval/run_eval.py eval/results                 # 全部用例
./.venv/Scripts/python.exe eval/run_eval.py eval/results 01_beijing_departure
./.venv/Scripts/python.exe eval/analyze.py eval/results                  # 汇总指标
./.venv/Scripts/python.exe eval/compare.py <旧结果目录> eval/results      # 与上一轮对比
```

`analyze.py` 统计：耗时、排程调用次数、最长单程、「周边自选」餐数、估算路段、绕路超 1km 的餐点、
未安排景点与未满足必吃、回复里在工具数据中找不到的店名（疑似编造）。
**改了排程引擎或提示词后，用它与上一轮对比再合并。**

### 高德录制回放（不耗额度、结果可复现）

`AMAP_CACHE=record` 时照常请求高德，并把成功的返回按「接口 + 参数」存进 `data/amap-cache.db`
（`AMAP_CACHE_PATH` 可改，键里不含 Key，出错的返回不存）；`AMAP_CACHE=replay` 时只读这份缓存、不联网，
未命中直接报错。默认关闭，线上不要开：录下来的驾车时间是录制那一刻的路况，营业时间、店铺也会变。

```bash
AMAP_CACHE=record ./.venv/Scripts/python.exe eval/run_eval.py eval/results/round8   # 跑测评时顺便录下高德返回
AMAP_CACHE=replay ./.venv/Scripts/python.exe eval/replay.py eval/results/round8/01_beijing_departure.json
```

`eval/replay.py` 按结果文件里记录的工具调用（参数原样）依次重放、重新排程，不调用模型：
改了排程引擎后，用同一份输入对比前后的行程。没录过的轮次（第 7 轮及以前）不设 `AMAP_CACHE` 时会实时请求高德。

### 行程合理性：规则检查、压力测试与模型点评

上面的指标只管「走不走得通」。2026-10-30 用户实测指出的折返、天黑进户外景区、一天两顿素食，一条也没查出来，
所以另有三层检查，从便宜到贵：

```bash
./.venv/Scripts/python.exe eval/plan_checks.py eval/results              # 规则检查已有结果，不产生费用
./.venv/Scripts/python.exe eval/stress.py 1 300                         # 随机 300 例，离线，不产生费用
./.venv/Scripts/python.exe eval/review.py eval/results --dry-run        # 只看提示词
./.venv/Scripts/python.exe eval/review.py eval/results 11_nanjing_3days # 模型点评，产生费用
```

- `plan_checks.py`：确定性规则，查折返进同一片区、白天户外景点日落后半小时还没结束、同一天两顿同类菜。
  `tests/test_plan_checks.py` 用当次的旧行程验证规则能查出来、修复后的排法不误报。
- `stress.py`：随机生成景点、餐厅（含子景点、室内场馆、同类菜系），先查走不走得通（应全为 0），
  再跑 `plan_checks` 统计合理性问题。数字只在同一种子下可比，改引擎前后各跑一次。
- `review.py`：把真实测评的行程整理成时间轴，连同规则已查出的问题交给模型，以游客视角找规则没覆盖到的问题，
  结果写到 `<结果目录>/review/`。模型配置用 `REVIEW_MODEL` / `REVIEW_BASE_URL` / `REVIEW_API_KEY`
  （可指向 Gemini 等与主 Agent 不同的模型），未设置时沿用 `LLM_*`。

模型点评用来**发现新的问题类型**，不当回归测试：意见不稳定、可能看错，每条都回到数据核实；
确认是真问题的，写进 `plan_checks.py` 并加单元测试，以后由规则自动检查。

## 6. 开发约定

1. **行程表由确定性 Python 生成，不由 LLM 直接写。** LLM 只负责理解需求、检索、准备参数与最终解读；
   模型只能引用工具返回的 POI id，接触不到坐标、评分、营业时间（原因见 [ARCHITECTURE.md](ARCHITECTURE.md#设计原则)）。
2. `app/` 与 Web 框架无关：`app/agent.py` 的 `chat_events()` 产出 SSE 文本帧，`server/` 只负责 HTTP、存储与取消信号。
3. 排程引擎 `app/engine/` 保持纯 Python、不访问网络；真实路网通过 `TravelTimes` 子类注入。
4. 工具失败时返回具体原因（`{"error": ...}`），不要当成空结果，否则模型会编造。
5. 不针对某个城市写专属规则；地方经验交给模型知识，引擎只做通用约束。
6. 提示词 `AGENT_INSTRUCTIONS` 是 f-string，里面的花括号要写成 `{{ }}`。
7. 业务包名是 `app`，**不要改回 `agents`**：会和 OpenAI Agents SDK 的 `agents` 包重名，`from agents import Agent` 会失败。

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 测试报 `cannot import name 'Agent' from 'agents'` | 用了系统 Python，没装 `openai-agents` | 用 `.venv` 里的 Python 运行 |
| 5173 页面发消息报 502 / 连接失败 | 后端没启动，或不在 8000 端口 | 先启动 `python -m server`；端口不同时设 `BACKEND_URL` |
| 8000 页面显示「前端尚未构建」 | 没有 `dist/` | `npm run build`，或开发时改用 5173 |
| `/health` 显示缺少配置 | `.env` 不在项目根目录或没填全 | 从项目根目录启动，补齐变量 |
| `curl` 本地服务 502 | 本机 HTTP 代理拦截 | 加 `--noproxy '*'` |
| 控制台中文乱码 | Windows 控制台编码 | 设置 `PYTHONIOENCODING=utf-8` |
| Windows 日志里偶尔有 `ConnectionResetError` | 浏览器断开连接时 asyncio 在 Windows 上的提示 | 无害，可忽略 |
