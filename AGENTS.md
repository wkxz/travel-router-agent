# AGENTS.md

本文件是 Agent 进入本工作目录时的第一入口。**开工前先读 `README.md`（现状与架构），再读 `HANDOFF.md`（环境与踩坑）。**

## 这是什么项目

**旅游路线生成 + 必吃推荐 Agent**（用户的学生期末项目）。

输入「想去的地方 + 想吃的东西 + 玩几天」→ 输出一份可直接照着走的行程单。

## 现状（2026-10-23）

**功能完整、可本地运行，尚未部署。** 后端 `agents/`、前端 `src/`、云函数 `cloud-functions/` 都已实现；
`tests/` 有 195 个不联网的单元/回归测试，`eval/` 有 10 条真实提示词的测评脚本。
代码在 `master` 分支，远程仓库 https://github.com/wkxz/travel-router-agent 。

`reference/` 里是**另一个已完成项目（travel-decision-agent）的只读副本**，
用于抄写法，**不是本项目的代码**。不要修改 `reference/` 下的任何文件。
注意：副本 `reference/agents/_tools.py:70-71` 会把含高德 Key 的 URL 拼进错误信息，本项目已修复，
项目 A 原件需要用户自己去修。

## 三条不可违背的规则

1. **密钥只从环境变量读**，绝不硬编码、绝不打印、绝不提交。
   `.env` 必须先在 `.gitignore` 里。不要读取项目 A 的 `.env` 后打印内容。

2. **`git push`、部署、删除文件必须先问用户**，不要自作主张执行。

3. **行程表由确定性 Python 生成，不由 LLM 直接写。**
   LLM 只做意图理解与最终解读。理由见 `docs/ANALYSIS.md` 第 5 节 ——
   LLM 直接写必然产出「看起来合理但根本走不通」的行程，这是本项目最大风险。

## 开工顺序

1. 读 `README.md`（架构、目录、运行、测试）
2. 读 `HANDOFF.md`（§5 环境与本地运行、§6 踩坑清单、§11 排程引擎的设计取舍）
3. 改代码前后都跑 `python -m unittest discover -s tests -t .`；改了排程或提示词，用 `eval/` 实测对比
4. 改了 `agents/` 后要重新 `python local-build-agent.py`，本地服务跑的是 `.edgeone/` 里的构建副本

## 用户偏好

- 中文交流
- 会质疑不确定的结论 → 不确定就实测核实，**不要嘴硬**
- 重视实测证据，不接受「文档上这么写的」
- 报问题给具体路径 + 行号 + 错误原文

## 关键事实速查

| 项 | 值 |
|---|---|
| 技术栈 | EdgeOne Makers + OpenAI Agents SDK + 高德 Web API |
| 本地开发 | `edgeone makers dev` 在 Windows 上会卡死 → 用手工构建 + uvicorn（HANDOFF §5.3） |
| 本机代理 | `curl` 本机服务必须加 `--noproxy '*'` |
| 必吃数据源 | 高德 POI 传 `extensions=all` 才有 `biz_ext`（评分/人均/营业时间） |
| 关键词检索 | 必须带 `citylimit=true`，否则 city=南京 搜「西湖」返回的全是杭州 |
| 高德限流 | 10019~10022 都是 QPS 超限，要退避重试；`/v3/distance` 起点最多 100 个，超出静默丢弃 |
| 不可用的数据源 | Wikipedia 系（被墙）、大众点评/美团（无公开 API） |
