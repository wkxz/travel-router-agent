# 项目交接文档（HANDOFF）

> **写给接手的 Agent**：这是你在本工作目录的入口文档，读完即可开工。
> 最后更新：2026-10-23（项目 B 已实现，见 §4.2；架构与运行方式见 `README.md`）

---

## 0. 三十秒看懂

用户是学生，**期末项目 = 做一个 Agent**。你现在的工作目录是 `travel-route-agent`，
也就是**项目 B**——这才是你要做的东西。

| | 项目 A | 项目 B（你在这） |
|---|---|---|
| 目录 | `../travel-decision-agent/`（工作目录**外**） | `.`（当前工作目录） |
| 名称 | 出行决策助手（找商场） | 旅游路线生成 + 必吃推荐 |
| 性质 | **参考案例**，已上线并交付 | **期末自选题** |
| 状态 | ✅ 完成，不要再改 | 🟢 功能完整、可本地运行、经多轮真实测评；未部署 |
| 你的角色 | 只读，当模板抄 | **主战场** |

**核心任务**：把项目 B 做出来。技术底座（EdgeOne Makers + OpenAI Agents SDK + 高德 API）
已经在项目 A 验证过。（早期用于抄写法的副本 `reference/` 已于 2026-10-29 删除，本项目不依赖它；下文提到它的地方仅作历史记录。）

---

## 1. 工作目录边界（先看这节，避免瞎找文件）

### 1.1 工作目录里有什么

> 下面是 2026-10-20 立项时的样子。现在已有 `agents/`、`src/`、`cloud-functions/`、`tests/`、`eval/` 等，见 `README.md` 的目录说明。

```
travel-route-agent/                  ← 你的 workspace
├── HANDOFF.md                       ← 本文件
├── README.md                        ← 项目简介
├── docs/
│   ├── ANALYSIS.md       219 行     ← 项目 B 要做什么（产品 + 技术分析）
│   ├── API-RESEARCH.md   160 行     ← 哪些数据源能用（全部实测）
│   └── NOTES.md           77 行     ← 实测踩坑记录
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

项目根目录 `.env.example` 里有变量模板：

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

### 3.2 目录结构（项目 A 原件）

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

**必须复用的三个工程模式**（本项目实现在 `agents/_data/amap.py`）：
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

### 4.2 当前进度（2026-10-23）

- ✅ 后端：5 个工具 + 确定性排程引擎 + 独立校验器；高德真实路网通勤；忌口/预算；缺口清单（gaps / next_step）
- ✅ 前端：聊天 + 行程卡片（无底图的路线示意图已按用户决定删除）
- ✅ 测试：`tests/` 195 个不联网用例；`eval/` 10 条真实提示词测评（已跑 8 轮，结果不入库）
- ✅ git：只有 `master` 一个分支，已推送到 https://github.com/wkxz/travel-router-agent
- ⬜ 部署到 EdgeOne、飞书介绍文档、效果截图

测评效果（第 1 轮 → 第 8 轮）：北京返程用例 150s/4 次排程 → 约 70s/1 次；忌口预算用例 255s/5 次 → 约 85s/1 次；
「周边自选」饭点 5 → 0；未发现编造店名。

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

### 4.4 决策项

- [x] 三档节奏（轻松 / 均衡 / 紧凑）：已实现（`pace` 参数，每日 2/3/4 个景点）
- [x] 技术栈：沿用 EdgeOne Makers + OpenAI Agents SDK
- [x] 地图：无底图的示意图「很抽象」，用户决定删除（2026-10-22）
- [ ] 飞书介绍文档：用连接器直接创建，还是生成 Markdown 让用户粘贴（待问）

### 4.5 当初的落地顺序（1~5 已完成，剩 5 的部署与 6）

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

**启动步骤**（脚本在项目根目录）：
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

### 5.4 `local-build-agent.py` 的模板路径

项目 B 的 `local-build-agent.py` 已改为：优先读环境变量 `EDGEONE_AGENT_TEMPLATE`，再试项目内
`node_modules/edgeone/...` 与原作者机器上的托管 CLI 路径；都找不到但 `.edgeone/agent-python/_platform`
已存在时，沿用已有运行时、只重建 `pages_agents/`。`local-dev.sh` 按脚本所在目录定位，不再写死路径。

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

### 项目 B 开发中新踩的坑（2026-10-22 ~ 23，均有回归测试）

| # | 现象 | 根因 | 解法 |
|---|---|---|---|
| 16 | city=南京 搜「西湖」返回 6 条杭州 | `/place/text` 默认不限城市 | 加 `citylimit=true` |
| 17 | 101 个起点只返回 100 条 | `/v3/distance` 起点上限 100，超出静默丢弃 | 按 100 分批 |
| 18 | 检索偶发失败 `CGQPS_HAS_EXCEEDED_THE_LIMIT` | 10022 也是 QPS 超限，旧代码只认 10021 | 10019~10022 统一退避重试 |
| 19 | 高德 4xx 时 Key 进了工具输出 | httpx 异常文本含完整 URL（含 `key=`） | 只报状态码，错误文本统一脱敏 |
| 20 | 模型漏传参数只收到 "An error occurred while running the tool" | SDK 默认 `DONT_LOG_TOOL_DATA`，校验细节被丢弃 | 必填项函数内自检并点名；自定义 `failure_error_function` |
| 21 | 「下周一」算错 | 提示词里没有当天日期 | `instructions` 改为回调，每次请求注入北京时间日期 |
| 22 | 复杂请求中途 `MaxTurnsExceeded` | SDK 默认 `max_turns=10` | 提高到 30（`AGENT_MAX_TURNS`） |
| 23 | 中山陵周末被判闭馆 | 「墓室仅周二至周五开放」被当成全景区开放日 | 忽略子设施的「仅…」子句 |
| 24 | 模型偶发空回复，用户看到空白 | 模型网关偶发，重跑不复现 | 无文字无行程时自动重试一次，仍空则提示重发 |
| 25 | 提示词里写 `{"id": ...}` 导致模块加载失败 | `AGENT_INSTRUCTIONS` 是 f-string | 花括号写成 `{{ }}` |
| 26 | 本地服务行为和源码不一致 | 本地跑的是 `.edgeone/` 构建副本 | 改了 `agents/` 必须重跑 `local-build-agent.py` |
| 27 | 厦门一天过海 4 趟（岛上午饭 → 市区小吃 → 岛上晚饭 → 回市区） | 挑餐厅按直线（1.5km 算「就近」），时间轴用路网（驾车 9.3km / 85 分钟） | 路网距离 ≥ 直线 3 倍且多出 ≥2km 记为绕行（`travel.is_roundabout`）：餐厅与空档景点跳过，主景点路段标「以地图导航为准」 |
| 28 | 3 天只排了 4 个景点，半天半天地空着 | 模型只传用户点名的景点，补充的都放进 backup_spots | 提示词要求 spots 总数达到「天数 × 每日上限」 |
| 29 | 本地经 vite 访问时停止按钮无效 | 前端请求 `/stop`（运行时自带路由，部署后可用），`vite.config.local.ts` 只转发 `/chat`、`/chat/stop` | `src/api.ts` 改为项目自己的 `/chat/stop`，两种环境都可用 |
| 30 | 武汉周二 18:00 晚餐排进 15:00 就关门的店，校验器也没拦 | 营业时间「周一至周四 06:00-15:00；周五至周日 06:00-20:00」两段时刻被合在一起判断 | `engine/hours.py` 的 `hours_on_date` 先取当天那段（星期条款、节假日特殊日期段、全天关闭），排程/分天/校验统一走它 |
| 31 | 大理 20:00 的火车，下午空着却省略告别餐 | 晚餐固定 18:00；告别餐时间按回住处前的位置算 | 返程日告别餐可提前到 16:00；回住处后重算；上午游完离发车 ≥6 小时时午餐按回住处方向挑 |
| 32 | 晚餐排进糕饼店、午餐排进疗养院职工餐厅 | 餐饮类型只按模型给的 meal_type | 高德子类为咖啡厅/茶艺馆/冷饮店/糕饼店/甜品店的不作正餐，店名含「职工餐厅」等的不选；用户点名的必吃除外 |
| 33 | 杭州 11 月雷峰塔按 08:00-20:00 排，实际冬季 17:30 关门 | 「03-16至04-30 …；11-01至03-15 …」「10月8日至3月31日 …」这类不写年份的季节段没被识别，三季时刻被合在一起 | `hours.py` 识别 `MM-DD` 与 `M月D日` 日期段，按月日比较、可跨年 |
| 34 | 午饭后紧接着排一家人均 ¥81 的中餐厅当「小吃」 | 小吃档只看模型检索时给的 meal_type | 小吃档人均超过 ¥60 且不是点名必吃的不选（`SNACK_MAX_COST`） |
| 35 | 手机（375px）上侧栏占半屏、输入框被挤没 | 只有桌面布局 | `≤640px` 时侧栏收成顶部一条（只留新建按钮）、工具灯只留图标、消息区内部滚动 |
| 36 | 杭州用户点名的雷峰塔被舍弃（「返程日时间不足」），第 1 天旁边模型补的太子湾却排得下、还空 1.5 小时 | 分天按估算车程判断放得下，排时间轴用真实车程（周日拥堵长约 60%）放不下；时间轴是逐天排的，掉了不会回头调整 | `scheduler._retry_with_swaps`：排时间轴掉了景点时，试着和别的天的景点对调（重算当天顺序）后整趟重排，先比点名景点少丢、再比总共排进的多，只在更好时采用；最多试 `SWAP_MAX_TRIES` 种 |

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

- [ ] 1. 读 `README.md` —— 架构、目录、运行、测试
- [ ] 2. 读本文件 §5、§6、§11 —— 本地运行方式、踩坑清单、排程引擎的设计取舍
- [ ] 3. 跑 `python -m unittest discover -s tests -t .`，确认 195 个全过
- [ ] 4. 确认 `.env` 里有可用的 Key（问用户，不要自己去翻项目 A 的 `.env`，也不要打印）
- [ ] 5. `python local-build-agent.py` + `bash local-dev.sh` + `npx vite --config vite.config.local.ts`，发一条请求试跑
- [ ] 6. 改排程或提示词后，用 `eval/` 跑真实测评并与上一轮 `compare`

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

### 手册里没有算法（已全文核查）

手册全文 213 段，**不含任何算法内容**。关键词实测：「算法」0 处、「复杂度」0 处、
「遍历」0 处、「最优」0 处、「贪心」0 处、「动态规划」0 处、「TSP」0 处、「缓存」0 处、「索引」0 处。
「排序」仅 1 处，出现在一句流程描述里（「计算距离 → 按条件排序」），指的是按距离排一下。

全文只有 1 段真 Python 代码（§9.2 的 `@function_tool` 示例），且原文明确写了
「下面代码只做讲解使用，实际无需自己编码」。

**它教的是工程配置**：Fork → 授权 → 配环境变量 → 用自然语言提需求 → 让 WorkBuddy 生成工具函数 → Push → 部署。
「计算距离」也是直接调高德 `/v3/distance`，Agent 自身不做任何计算。

**对项目 B 的含义**：本项目要写的「最近邻 + 2-opt + 按天切分 + 可行性校验」
是手册**完全没有覆盖**的部分 —— 既是加分空间（能体现真实技术含量），
也意味着**没有参考实现**，算法层必须自己写、自己验证（这也是为什么 §4.5 建议先独立跑通排程引擎）。

**只有在用户明确要求「照着手册做一遍」时**才去读，且只需读相关那几页。

---

## 11. 排程引擎的设计取舍（改之前先看，参数都有实测依据）

| 机制 | 位置 | 要点 |
|---|---|---|
| 分天 | `engine/calendar_pack.py` | 景点 ≤10 个时状态压缩 DP 穷举；目标依次为：排进去最多（用户点名 `must` 舍弃代价 10 倍）→ 通勤最短 → 负荷平方和均衡。可行性：通勤按直线估算 ×1.25（实测高德驾车普遍长约 30%）、返程日按末站到车站实际估算、夜游不占白天预算、营业时段用 `timeline.py` 模拟 |
| 夜游 | `split_evening` | 晚餐后窗口 175 分钟；放不下的改白天并提示；留谁在晚上看全天路程；返程日晚上不可用 |
| 通勤 | `engine/travel.py` + `_data/routing.py` | 时间轴与校验器用高德路网；挑候选只用估算（避免为每个候选请求）；取不到时回退估算并标「估算」 |
| 餐厅 | `scheduler._pick_best_restaurant` | 按「绕路距离」排序；离当前位置 ≤2.5km 或绕路 ≤1km 可选，绕路 >4km 一律不选；按真实路段绕行明显（隔水等）的跳过；无预算时人均 >150 降权 |
| 必吃 | 同上 | 绕路 ≤1km 强制优先，返程日 2km，最后一顿 3km；早茶类不排晚餐；排不进写 `unmet_must_eat` + `gaps` |
| 补救 | `_tools.next_step` | gaps 非空：按建议并行补检索、只重排一次；gaps 为空：不要重排、不要自行删减景点 |
| 校验 | `engine/validator.py` | 时间重叠、通勤时间够不够、营业时间、闭馆、餐厅/必吃重复、到站缓冲；有 error 就拒绝输出 |
