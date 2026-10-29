# 开发指南

## 1. 环境要求

| 依赖 | 版本（开发时使用） | 说明 |
|---|---|---|
| Python | 3.11 | Agent 与排程引擎 |
| Node.js | 22 / 24 | 前端构建与 EdgeOne CLI |
| EdgeOne CLI | npm 包 `edgeone` | 本地构建需要它自带的 agent-python 运行时模板 |

```bash
# Python 依赖
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # macOS / Linux 用 .venv/bin/python

# 前端依赖
npm install
```

## 2. 配置密钥

复制 `.env.example` 为 `.env` 并填写（变量说明见 [DEPLOYMENT.md](DEPLOYMENT.md#22-密钥)）：

```
AI_GATEWAY_API_KEY=
AI_GATEWAY_BASE_URL=
AI_GATEWAY_MODEL=
AMAP_API_KEY=
```

`.env` 已在 `.gitignore` 中。**密钥只从环境变量读取**：不要硬编码、不要打印、不要提交。
返回给模型或写进日志的错误文本都会先经过 `agents/_data/amap.py` 的 `redact_secrets` 脱敏。

## 3. 本地运行

官方方式是 `edgeone makers dev`（`npm run dev:agents`），但它在 **Windows 上会卡死**（CLI 1.6.41 的已知问题）。
项目提供手工构建 + uvicorn 的方式，各平台通用：

```bash
# 1. 构建：把 agents/ 复制为 .edgeone/agent-python/pages_agents/ 并改写导入路径，拷入运行时模板
./.venv/Scripts/python.exe local-build-agent.py

# 2. 启动 Agent（http://127.0.0.1:8088）
bash local-dev.sh                 # PowerShell：./local-dev.ps1

# 3. 启动前端（http://localhost:5173，/chat、/chat/stop、/health 代理到 8088）
npx vite --config vite.config.local.ts
```

要点：

- **改了 `agents/` 下的代码，必须重新执行第 1 步。** 本地服务运行的是 `.edgeone/` 里的构建副本，不是源码。
- 构建脚本按以下顺序查找运行时模板：环境变量 `EDGEONE_AGENT_TEMPLATE` → 项目内 `node_modules/edgeone/edgeone-dist/pages/templates/agent-python`。
  都找不到、但 `.edgeone/agent-python/_platform` 已存在时，沿用已有运行时，只重建业务代码。
- 本地前端只代理 Agent 路由，**会话列表、历史等云函数接口本地不可用**（返回 404），部署后正常；
  刷新页面时会话从浏览器本地快照恢复。
- 本机配置了 HTTP 代理时，`curl` 访问本地服务要加 `--noproxy '*'`，否则会被代理拦截返回 502。

快速验证 Agent：

```bash
curl --noproxy '*' -N -X POST http://127.0.0.1:8088/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"杭州玩 2 天，想去西湖和灵隐寺，想吃片儿川"}'
```

## 4. 测试

```bash
# 单元 / 回归测试：不联网、不花钱，约 3 秒
./.venv/Scripts/python.exe -m unittest discover -s tests -t .

# 前端类型检查与生产构建
npx tsc --noEmit -p .
npm run build
```

- `tests/` 共 195 个用例，用假数据（`tests/fake_amap.py`）覆盖排程、校验、工具层与安全脱敏。
  测试直接导入源码 `agents/`，不需要先构建。
- **修 bug 先写能复现问题的测试**，并确认撤掉修复后该测试会失败，避免写出「怎么都能过」的空测试。
- `tests/test_eval_regressions.py` 里有一条「分天计算 2 秒内完成」的耗时断言，机器繁忙时偶尔会超时，重跑即可。

## 5. 真实环境测评

`eval/` 用 10 条真实提示词在进程内运行与线上相同的 Agent，记录工具调用、行程与回复。
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

## 6. 开发约定

1. **行程表由确定性 Python 生成，不由 LLM 直接写。** LLM 只负责理解需求、检索、准备参数与最终解读；
   模型只能引用工具返回的 POI id，接触不到坐标、评分、营业时间（原因见 [ARCHITECTURE.md](ARCHITECTURE.md#设计原则)）。
2. 排程引擎 `agents/engine/` 保持纯 Python、不访问网络；真实路网通过 `TravelTimes` 子类注入。
3. 工具失败时返回具体原因（`{"error": ...}`），不要当成空结果，否则模型会编造。
4. 不针对某个城市写专属规则；地方经验交给模型知识，引擎只做通用约束。
5. 提示词 `AGENT_INSTRUCTIONS` 是 f-string，里面的花括号要写成 `{{ }}`。

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `edgeone makers dev` 卡住无输出 | CLI 在 Windows 上的缺陷 | 用第 3 节的手工构建 + uvicorn |
| `404 No handler for /chat` | `AGENT_ROUTE_TABLE` 的键写成了正则 | 键必须是精确路径 `/chat` |
| `ImportError: pages_agents` | 构建时把 `from agents import Agent` 也改写了 | 只改写 `from agents.<模块>` 形式的导入（`local-build-agent.py` 已处理） |
| 本地行为和源码不一致 | 运行的是 `.edgeone/` 构建副本 | 重新执行 `local-build-agent.py` 并重启服务 |
| `curl` 本地服务 502 | 本机 HTTP 代理拦截 | 加 `--noproxy '*'` |
| 测试报 `cannot import name 'Agent' from 'agents'` | 用了系统 Python，没装 `openai-agents` | 用 `.venv` 里的 Python 运行 |
| 控制台中文乱码 | Windows 控制台编码 | 设置 `PYTHONIOENCODING=utf-8` |
