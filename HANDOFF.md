# 项目交接文档（HANDOFF）

> **写给接手的 Agent**：这是你在本工作目录的入口文档，读完即可开工。
> 最后更新：2026-10-20 18:05

---

## 0. 三十秒看懂

用户是学生，**期末项目 = 做一个 Agent**。你现在的工作目录是 `travel-route-agent`，
也就是**项目 B**——这才是你要做的东西。

| | 项目 A | 项目 B（你在这） |
|---|---|---|
| 目录 | `../travel-decision-agent/`（工作目录**外**） | `.`（当前工作目录） |
| 名称 | 出行决策助手（找商场） | 旅游路线生成 + 必吃推荐 |
| 性质 | **参考案例**，已上线并交付 | **期末自选题**，仅完成分析，无代码 |
| 状态 | ✅ 完成，不要再改 | 🟡 4 份文档 + 只读参考副本，等用户拍板后开工 |
| 你的角色 | 只读，当模板抄 | **主战场** |

**核心任务**：把项目 B 做出来。技术底座（EdgeOne Makers + OpenAI Agents SDK + 高德 API）
已经在项目 A 验证过，`reference/` 里有它的只读副本可以直接抄。

---

## 1. 工作目录边界（先看这节，避免瞎找文件）

### 1.1 工作目录里有什么

```
travel-route-agent/                  ← 你的 workspace
├── HANDOFF.md                       ← 本文件
├── README.md                        ← 项目简介
├── docs/
│   ├── ANALYSIS.md       219 行     ← 项目 B 要做什么（产品 + 技术分析）
│   ├── API-RESEARCH.md   160 行     ← 哪些数据源能用（全部实测）
│   └── NOTES.md           77 行     ← 实测踩坑记录
└── reference/                       ← 项目 A 的只读副本（213K）
    ├── README.md                    ← 每个文件干什么、怎么抄
    ├── agents/                      ← _tools.py（★核心范本）、chat/index.py、_logger.py
    ├── config/                      ← edgeone.json、package.json、tsconfig、requirements、.env.example
    ├── tools/                       ← local-build-agent.py、local-dev.sh（Windows 绕过方案）
    └── src/                         ← 完整 React 前端骨架
```

### 1.2 工作目录外有什么（需要时找用户要，不要自己乱翻）

| 东西 | 路径 | 说明 |
|---|---|---|
| 项目 A 原件 | `D:\桌面\上课\神经网络\travel-decision-agent` | 完整可运行版本，含 `.env`、`submission/`、`node_modules` |
| 真实凭据 | 同上目录的 `.env` | **不在 workspace 内**，见 §2 |
| 课程手册 | `D:\桌面\上课\神经网络\04_课程讲义与手册\...docx` | 8.7MB，**默认不要读**（有错，见 §9） |

**为什么凭据不在 workspace 里**：这是刻意的。密钥不应该出现在工作目录，避免被误提交、误打包、误打印。
需要调真实 API 时，按 §2.2 处理。

---

## 2. 账号与凭据

### 2.1 账号信息

| 项 | 值 |
|---|---|
| GitHub | `wkxz`（显示名 `wkxy`） |
| EdgeOne 账号 ID | `100035726303` |
| 线上项目 | `travel-agent-git`（内部 ID `makers-pos71powjrok`） |
| GitHub 仓库 | `https://github.com/wkxz/travel-decision-agent` |
| 上游模板 | `TencentEdgeOne/openai-agent-starter-python` |
| EdgeOne CLI Token | 项目 A 目录的 `.edgeone-token`（已在 .gitignore） |

### 2.2 需要凭据时怎么办

`reference/config/.env.example` 里有 4 个变量的模板：

| 变量 | 用途 | 敏感度 |
|---|---|---|
| `AI_GATEWAY_API_KEY` | LLM Key | **高 —— 能烧钱，绝不要索取明文** |
| `AI_GATEWAY_BASE_URL` | OpenAI 兼容端点（含 `/v1`） | 低 |
| `AI_GATEWAY_MODEL` | 模型名，默认 `@makers/deepseek-v4-flash` | 低 |
| `AMAP_API_KEY` | 高德 Web 服务 Key | 中 —— 能烧配额 |

**正确做法**：
1. 在项目 B 根目录建 `.env`（**必须先加进 `.gitignore`**），让**用户自己**填值
2. 或者让用户通过环境变量注入
3. **不要**让用户在聊天里粘贴 Key 明文，也不要读取项目 A 的 `.env` 后打印出来

⚠️ **硬性要求**：Key 只从环境变量读，绝不硬编码、绝不打印到日志、绝不提交到 git。
⚠️ **关键操作（git push、部署、删除文件）必须先问用户确认**，不要自己执行。

---

## 3. 项目 A 是什么（参考案例，已完成）

### 3.1 它做什么

用户说「我在北京朝阳合生汇附近，想找一个同时有倍轻松和西町村屋的商场」，
Agent 解析地点 → 搜附近商场 → 逐个核对品牌 → 测距 → 给出推荐。

属于「**单点核验**」型：找一个同时满足 A、B 条件的最近地点。
**和你做的项目 B 不是一个难度量级**，别以为照抄就完事。

### 3.2 目录结构（原件视角，`reference/` 是其子集）

```
travel-decision-agent/
├── agents/                    ← 【后端核心】EdgeOne 自动路由
│   ├── _tools.py              ← 四个高德工具（★ 最重要）
│   ├── _logger.py             ← 日志封装
│   └── chat/
│       ├── index.py           ← POST /chat（Agent 定义 + 提示词 + SSE 流）
│       └── stop.py            ← POST /chat/stop（中断）
├── cloud-functions/           ← 会话管理（history / conversations / delete / clear）
├── src/                       ← 前端（React + Vite，中文界面）
├── edgeone.json               ← 部署配置
├── requirements.txt           ← Python 依赖
├── package.json               ← 前端 + SDK 依赖
├── .env.example               ← 环境变量模板
├── local-build-agent.py       ← ★ Windows 本地开发绕过方案
├── local-dev.sh               ← ★ 启动本地 agent 服务
├── vite.config.local.ts       ← 本地前端代理配置
└── submission/                ← 交付材料（含个人信息，不要碰）
```

### 3.3 请求链路

```
浏览器 → POST /chat {message, userId}
   → agents/chat/index.py handler(context)
   → Runner.run_streamed(agent, ...)  ← OpenAI Agents SDK
   → 模型决定调用工具 → agents/_tools.py → 高德 Web API
   → SSE 回流：event: text_delta / tool_called / error / done
   → 前端逐字渲染 + 工具指示灯
```

`context` 契约（写新 handler 时照抄）：
- `context.request.body` — dict
- `context.request.signal` — asyncio.Event，`/chat/stop` 时被 set
- `context.conversation_id` / `context.run_id`
- `context.store` — 内置存储，`openai_session(cid)` 提供会话记忆

### 3.4 四个工具（项目 B 的写法范本）

| 工具 | 高德接口 | 关键实现点 |
|---|---|---|
| `geocode_location` | `/v3/geocode/geo` | 可选 `city` 参数提高准确率 |
| `search_nearby_malls` | `/v3/place/around` | `types=060100`、`sortrule=distance`、**`extensions=all`** |
| `check_brand_in_mall` | `/v3/place/around` | 小半径(300m)搜品牌 + 用 `mall_name` 二次校验剔除隔壁商场误命中 |
| `measure_distance` | `/v3/distance` | `type`: 0=直线 1=驾车 |

**必须复用的三个工程模式**（都在 `reference/agents/_tools.py`）：
1. `_throttle()` — 全局最小间隔 0.35s（高德免费 Key QPS 极低）
2. `_amap_get()` — 统一注入 Key + 节流 + QPS 退避重试 + `status=="1"` 校验
3. 错误统一返回 `{"error": "..."}`，**让模型能解释失败原因**，绝不当成空结果

### 3.5 线上信息

- 访问地址：`https://travel-agent-git-xinwopd9.edgeone.dev/`
  默认开启访问保护，必须带 `?eo_token=...&eo_time=...`（3 小时有效），首次访问后下发 Cookie
- 当前线上版本：commit `38afb36`，部署 ID `dpk5o50hfb16`（Production）
- 签名过期后：控制台点「预览」重新获取

---

## 4. 项目 B：你要做的事

### 4.1 定位

**输入「想去的地方 + 想吃的东西 + 玩几天」→ 输出一份可直接照着走的行程单。**

与项目 A 的本质差别：

| 维度 | 项目 A（找商场） | 项目 B（行程编排） |
|---|---|---|
| 类型 | 单点核验 | **多目标时空编排** |
| 工具调用 | 9~17 次 | **预计 40~80 次** |
| 是否需要算法 | 否 | **是**：最近邻 + 2-opt + 按天切分 |
| 最大风险 | 查不到 → 换一个 | **产出「看起来合理但根本走不通」的行程** |

### 4.2 当前进度（诚实版）

**只有文档，没有一行代码。** 没有 `package.json`、没有 `git` 仓库、没有 `agents/`。
`reference/` 里全是**只读副本**，不是项目 B 的代码。

### 4.3 已经定下来的设计决策（不要推翻）

1. **LLM 只做意图理解 + 最终解读，行程表由确定性 Python 生成**
   原因：LLM 直接写行程表必然幻觉出「看起来对但走不通」的路线，这是本项目最大风险。
   架构：`意图解析 Agent → POI 检索层 → 排程引擎(纯 Python) → 校验层 → 渲染层`

2. **餐饮双轨制**（用户明确纠正过）
   - 正餐 60–90 min，固定占午餐/晚餐
   - 特色小吃 20–45 min，穿插填空档
   - 夜市/小吃街 1.5–2 h，当作一个完整事件占晚餐时段
   → 行程不是「2 点 + 2 餐」的机械结构，而是用**时段模板**填混合序列

3. **高德没有「小吃」分类码**（实测）
   `050400` 实际是茶饮/咖啡，`050900` 实际是甜品店，地方小吃全在 `050100 中餐厅` 下。
   → 正确筛法：`types=050000` + `keywords`（菜系/品类词）+ 对 `poi.type` 字符串二次过滤

4. **必吃推荐的数据源就是高德自己**
   `extensions=all` 返回 `biz_ext`（rating / cost / open_time），实测覆盖率 100%/92%/88%。
   不需要第三方点评 API（国内也没有可用的免费版），不需要人工兜底清单。

### 4.4 待用户拍板（开工前必须问清，不要自己决定）

- [ ] 三档节奏（轻松 / 均衡 / 紧凑）做不做
- [ ] 技术栈是否沿用 EdgeOne Makers + OpenAI Agents SDK（建议沿用，复用部署链路）
- [ ] 飞书介绍文档：用连接器直接创建，还是生成 Markdown 让用户粘贴

### 4.5 建议的落地顺序

1. `git init` + 从 `reference/config/` 拷 `package.json` / `tsconfig.json` / `vite.config.ts` / `edgeone.json`
   + 从 `reference/config/` 拷 `.env.example` 并建 `.gitignore`（务必先忽略 `.env`）
2. 先写**纯 Python 排程引擎**（不接 LLM、不接网络），用假数据跑通「输入 5 个点 → 输出 3 天行程」
   → 这是项目成败的核心，必须先独立验证
3. 再写 `agents/_tools.py`（抄 `reference/agents/_tools.py` 的 `_amap_get` + 节流 + `extensions=all`）
4. 把排程引擎包成工具暴露给 Agent
5. 提示词 + 前端（`reference/src/` 可整目录复制后改）+ 部署
6. 飞书文档 + 截图

---

## 5. 环境与运行手册

### 5.1 前置

- Node：`C:\Users\K\.workbuddy-ai\binaries\node\versions\22.22.2-6\node.exe`（托管版优先）
- Python：项目 A 内有 `.venv`（3.12）。项目 B 需自建：
  ```bash
  <managed-python> -m venv .venv
  ./.venv/Scripts/python.exe -m pip install -r requirements.txt
  ```
- **本机有 HTTP 代理** → `curl` 访问本机服务必须加 `--noproxy '*'`，否则 502

### 5.2 ⚠️ Windows 上 `edgeone makers dev` 会卡死

CLI 1.6.41 的 agent-python runner 在 Windows 上有缺陷，启动后挂住。
**这是已知问题，不要去修 CLI。**

### 5.3 绕过方案：手工复现构建 + 直接跑 uvicorn

CLI 构建期做两件事（本地必须自己复现）：
1. 把 `agents/` 拷成 `pages_agents/`
2. 把 `from agents.X`（**绝对路径导入**）重写成 `from pages_agents.X`
   ⚠️ **只改这种**，`from agents import Agent` 这种符号导入**不能动**，改了会 ImportError
   ⚠️ 业务代码内部用相对导入（`from .._tools import`），**不需要重写**

**启动步骤**（把 `reference/tools/` 的两个脚本拷到项目 B 根目录，改掉里面的绝对路径后使用）：
```bash
# 1. 生成 .edgeone/agent-python/ 构建产物
./.venv/Scripts/python.exe local-build-agent.py

# 2. 启动 agent 服务（8088）
bash local-dev.sh
```

`local-dev.sh` 的关键内容：
```bash
export PYTHONPATH=".edgeone/agent-python"
export AGENT_MODE=dev
export AGENT_TIMEOUT=300
export AGENT_ROUTE_TABLE='{"/chat":{"module":"pages_agents.chat.index","isIndex":true},"/chat/stop":{"module":"pages_agents.chat.stop","isIndex":false}}'
./.venv/Scripts/python.exe -m uvicorn _platform.adapter:app --host 127.0.0.1 --port 8088
```

⚠️ `AGENT_ROUTE_TABLE` 的键是**精确路径**（`/chat`），**不是正则**（写 `^/chat$` 会 404 "No handler for /chat"）。

**验证**：
```bash
curl --noproxy '*' -N -X POST http://127.0.0.1:8088/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"我在北京朝阳合生汇附近，想找同时有倍轻松和西町村屋的商场"}'
```

**前端本地跑**：
```bash
npx vite --config vite.config.local.ts    # 5173，/chat 代理到 8088
```

### 5.4 `local-build-agent.py` 的可移植性坑

`reference/tools/local-build-agent.py` 里硬编码了模板路径和项目路径：
```python
TPL = r'C:/Users/K/.workbuddy-ai/binaries/node/workspace/node_modules/edgeone/edgeone-dist/pages/templates/agent-python'
```
`local-dev.sh` 里也硬编码了 `cd "D:/桌面/上课/神经网络/travel-decision-agent"`。

**拷到项目 B 用之前必须先改这两处**，并确认 `TPL` 路径在本机存在，否则脚本静默失败。

### 5.5 部署

- 方式：**GitHub 绑定自动部署**（仓库里 **0 个 workflow**，不是 GitHub Actions）
- 流程：`git push` → 控制台自动构建 → 部署
- 环境变量（控制台 → 项目 → 环境变量，共 4 个）：见 §2.2 表格

### 5.6 换模型

**EdgeOne 内置网关只有 `@makers/deepseek-v4-flash` 可用**
（`/v1/models` 列表接口不存在，猜的其他模型名全部 400）。

想用自己的模型：改 3 个 `AI_GATEWAY_*` 变量指向自己的 OpenAI 兼容平台。
**前提**：该平台必须支持 **function calling + 流式输出**，否则 Agent 直接不能用。

---

## 6. 踩坑清单（★ 最有价值的部分，逐条都是实测）

| # | 现象 | 根因 | 解法 |
|---|---|---|---|
| 1 | `makers dev` 卡死无输出 | CLI 1.6.41 Windows 缺陷 | 用 §5.3 手工构建 + uvicorn |
| 2 | `404 No handler for /chat` | `AGENT_ROUTE_TABLE` 写了正则 | 键改精确路径 `/chat` |
| 3 | `ImportError: pages_agents` | 重写规则把 `from agents import Agent` 也改了 | 只重写 `from agents.<模块>` |
| 4 | `curl` 本机 502「积极拒绝」 | 本机 HTTP_PROXY 拦截 | 加 `--noproxy '*'` |
| 5 | 后台进程启动后立刻消失 | Windows bash `&` 被回收 | 用 `run_in_background=true`，别用 `&` |
| 6 | 高德返回 `10021` | 免费 Key QPS 极低（约 50 连发） | 全局节流 0.35s + 退避重试 |
| 7 | 高德返回 `10009` | Key 平台类型选错（选了 Web端JS 而非 Web服务） | 不可改，只能新建 Key |
| 8 | 搜索 0 结果但店确实存在 | 高德周边搜索是**严格匹配**，错一个字就 0 | 回退到 category 搜 + 提示用户核对写法 |
| 9 | 按 `types=050400` 筛小吃得到茶饮 | 分类码语义与直觉不符 | 用 `keywords` + `poi.type` 字符串二次过滤 |
| 10 | POI 没有评分/人均 | 没传 `extensions=all`（默认 `base`） | 显式传 `extensions=all` |
| 11 | 线上域名 401 | 预览域名默认开启访问保护 | 调 `DescribePagesEncipherToken` 拿 `?eo_token=&eo_time=` → 302 下发 Cookie（3h） |
| 12 | 页面白屏、请求 404 | 前端调了不存在的云函数路由 | 核对 `cloud-functions/<name>/index.py` 的目录名即路由名 |
| 13 | 换模型后 Agent 不调工具 | 新平台不支持 function calling | 换平台，或退回内置模型 |
| 14 | Wikipedia 接口本地通、线上挂 | EdgeOne 跑腾讯云**国内节点**，出网同本机 | 不要接 Wikipedia 系（见 §7） |
| 15 | 工具返回塞爆上下文 | 高德单条 POI 有 40+ 字段 | 工具层只回传必要字段（6 个左右） |

**通用教训**：这个项目里所有「文档说的」和「直觉以为的」都被实测推翻过至少一次
（品牌名错字、分类码语义、CLI 可用性、模型清单）。**任何结论先实测再说。**

---

## 7. 数据源结论（详见 `docs/API-RESEARCH.md`）

### 7.1 可用（零成本）

| 来源 | 用途 | 备注 |
|---|---|---|
| 高德 `/place/{around,text}` + `extensions=all` | 地点 + 评分 + 人均 + 营业时间 | **主力数据源** |
| 高德 `/v3/weather/weatherInfo` | 3 天预报 | `city` 要 **adcode**，不是城市名 |
| 高德 `/v3/config/district` | 地名 → adcode | 天气的前置 |
| 高德 `/v3/direction/*` | 步行/驾车/公交时长 | 排程必需 |
| Open-Meteo（免 Key） | 逐小时天气 + 降水概率 | 比高德天气细 |
| OSRM（免 Key） | 路线时长/距离 | 备用 |
| MyMemory（免 Key） | 菜名中英翻译 | — |

### 7.2 不可用（实测）

| 来源 | 实测结果 |
|---|---|
| `zh/en.wikipedia.org`、`commons.wikimedia.org` | ❌ 000 超时（被墙） |
| `www.wikidata.org` | ⚠️ 时好时坏，不能做生产依赖 |
| 大众点评 / 美团 / 饿了么 | ❌ 无公开免费 API |
| Yelp / Foursquare | ⚠️ 国内数据空白 |
| LibreTranslate / Pexels | ❌ 403 / 401 |

**结论**：景点背景讲解交给**模型自身知识**，外部 API 只负责**实时性信息**（天气/营业时间/距离/评分）。

---

## 8. 接手后的第一小时（验收清单）

按顺序做完，你就算真正接手了：

- [ ] 1. 读 `docs/ANALYSIS.md`（219 行，10 节）—— 理解项目 B 要做什么
- [ ] 2. 读 `docs/API-RESEARCH.md` —— 知道哪些数据源能用、哪些是坑
- [ ] 3. 读 `reference/README.md` —— 知道 `reference/` 每个文件干什么、怎么抄
- [ ] 4. 读 `reference/agents/_tools.py` —— 工具层的写法范本（重点看 `_throttle` 和 `_amap_get`）
- [ ] 5. 读 `reference/agents/chat/index.py` —— handler + 提示词的范本
- [ ] 6. 确认本机有可用的 `AMAP_API_KEY`（问用户，不要自己去翻项目 A 的 `.env`）
- [ ] 7. 检查 `reference/tools/local-build-agent.py` 里的 `TPL` 路径在本机是否存在
- [ ] 8. **向用户确认 §4.4 的三个待决策项**，然后才开始写代码

---

## 9. 工作方式约定（用户偏好，务必遵守）

- **中文交流**
- **会质疑不确定的结论**（「你确定有这个选项？」）→ 不确定就实测/截图核实，**不要嘴硬**
- **关键操作（git push、部署、删文件）必须先问，不要自作主张**
- 用户重视**实测证据**，不接受「文档上这么写的」作为结论
- 报告问题时给**具体路径 + 行号 + 错误原文**，不要模糊描述

---

## 10. 文档索引

| 文档 | 内容 |
|---|---|
| `docs/ANALYSIS.md` | 项目 B 产品与技术分析（10 节，含架构图、MVP 边界、风险、飞书大纲） |
| `docs/NOTES.md` | 项目 B 实测记录（分类码纠正、需求澄清过程） |
| `docs/API-RESEARCH.md` | 数据源调研（全部接口实测结果 + 覆盖率 + 落地要点） |
| `reference/README.md` | 项目 A 副本的逐文件说明 + 两个必须照抄的工程模式 |

### 课程手册：默认不要读

`04_课程讲义与手册/EdgeOne Makers - WorkBuddy智能体搭建手册.docx` 有 **8.7MB / 30 张图**，
读一遍消耗大量上下文，而其中有价值的信息**本文件已全部提炼**（§5 环境运行手册、§6 踩坑清单）。

更关键的是它本身有错，读它等于**重新踩一遍已经踩过的坑**：

1. 品牌名「西厅村屋」是错字 → 真实是「西町村屋」
2. 6.1 场景（北京红莲南路找合生汇）现实无解 → 改用「北京朝阳合生汇」
3. 全文没提 CLI 的安装与使用
4. 第十一章「完整代码」整章空白
5. 缺 git 环境检查步骤
6. 部分 URL 多了个 `s`
7. 5.1「进入自定义智能体」、6.3「本地运行排查」两节**一张图都没有**（最容易卡）

**只有在用户明确要求「照着手册做一遍」时**才去读，且只需读相关那几页。
