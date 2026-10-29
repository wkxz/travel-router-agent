# AGENTS.md

给在本仓库工作的 AI 编程助手的说明。开工前先读 `README.md`，再按需读 `docs/` 下的文档：

- `docs/DEVELOPMENT.md`：环境、本地运行、测试与测评、开发约定、常见问题
- `docs/DEPLOYMENT.md`：EdgeOne Makers 部署
- `docs/ARCHITECTURE.md`：设计原则、排程引擎算法与参数依据（改引擎前先看）

## 现状

功能完整、可本地运行，尚未部署。代码在 `master` 分支，远程仓库 https://github.com/wkxz/travel-router-agent 。

## 不可违背的规则

1. **密钥只从环境变量读**，绝不硬编码、绝不打印、绝不提交。`.env` 已在 `.gitignore` 中。
2. **`git push`、部署、删除文件必须先问用户**，不要自作主张执行。
3. **行程表由确定性 Python 生成，不由 LLM 直接写。** LLM 只做意图理解、检索与最终解读，
   理由见 `docs/ARCHITECTURE.md`「设计原则」。

## 改代码的流程

1. 改代码前后都跑 `./.venv/Scripts/python.exe -m unittest discover -s tests -t .`；
   修 bug 先写能复现的测试，并确认撤掉修复后它会失败。
2. 改了 `agents/` 后要重新执行 `local-build-agent.py` 并重启服务：本地服务跑的是 `.edgeone/` 里的构建副本。
3. 改了排程引擎或提示词，用 `eval/` 实测并与上一轮对比（会产生费用，先问用户）。
4. 改了前端，跑 `npx tsc --noEmit -p .`，并在浏览器里实际验证（含手机宽度）。

## 用户偏好

- 中文交流
- 会质疑不确定的结论 → 不确定就实测核实，**不要嘴硬**
- 重视实测证据，不接受「文档上这么写的」
- 报问题给具体路径 + 行号 + 错误原文

## 关键事实速查

| 项 | 值 |
|---|---|
| 本地开发 | `edgeone makers dev` 在 Windows 上会卡死 → 手工构建 + uvicorn（`docs/DEVELOPMENT.md` 第 3 节） |
| 本机代理 | `curl` 本机服务必须加 `--noproxy '*'` |
| 测试用的 Python | 必须用 `.venv`，系统 Python 没装 `openai-agents` |
| 必吃数据源 | 高德 POI 传 `extensions=all` 才有 `biz_ext`（评分/人均/营业时间） |
| 关键词检索 | 必须带 `citylimit=true`，否则 city=南京 搜「西湖」返回的全是杭州 |
| 高德限流 | 10019~10022 都是 QPS 超限，要退避重试；`/v3/distance` 起点最多 100 个，超出静默丢弃 |
| 不可用的数据源 | Wikipedia 系（被墙）、大众点评/美团（无公开 API） |
