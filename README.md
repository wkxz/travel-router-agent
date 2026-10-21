# travel-route-agent

旅游路线生成 + 必吃推荐 Agent（期末自选题）

**状态：仅分析阶段，尚未编码。**

输入「想去的地方 + 想吃的东西 + 玩几天」→ 输出一份可直接照着走的行程单。

## 接手入口

| 文件 | 说明 |
|---|---|
| **[`AGENTS.md`](AGENTS.md)** | Agent 入口：三条不可违背的规则 + 开工顺序 |
| **[`HANDOFF.md`](HANDOFF.md)** | **完整交接文档**：工作目录边界、账号、环境、15 条踩坑清单、待决策项 |

## 目录结构

```
travel-route-agent/
├── AGENTS.md              ← Agent 入口
├── HANDOFF.md             ← 完整交接文档
├── README.md              ← 本文件
├── .gitignore             ← 已忽略 .env / node_modules / .venv
├── docs/                  ← 项目 B 的设计与调研
│   ├── ANALYSIS.md        ← 产品与技术分析（10 节）
│   ├── API-RESEARCH.md    ← 数据源调研（全部实测）
│   └── NOTES.md           ← 实测踩坑记录
└── reference/             ← 项目 A 的只读副本（抄写法用，不要改）
    ├── README.md          ← 逐文件说明 + 两个必须照抄的工程模式
    ├── agents/            ← _tools.py（★核心）、chat/index.py、_logger.py
    ├── config/            ← edgeone.json、package.json、tsconfig、requirements、.env.example
    ├── tools/             ← local-build-agent.py、local-dev.sh
    └── src/               ← React 前端骨架
```

## 核心设计原则

**LLM 只做意图理解与解读，行程表由确定性 Python 生成 + 可行性校验层**，
避免「看起来合理但走不通」的行程。

## 参考项目

`../travel-decision-agent/` —— 已完整跑通并上线的参考案例
（EdgeOne Makers + OpenAI Agents SDK + 高德 API）。工作目录内已放置其只读副本于 `reference/`。
