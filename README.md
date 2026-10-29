# 旅游路线与必吃推荐助手

说出「想去的地方 + 想吃的东西 + 玩几天」，生成一份**可以直接照着走**的行程单：
每天去哪、按什么顺序、怎么过去、几点在哪家店吃什么，返程日按车次倒推、保证赶得上车。

> 例：「杭州玩 2 天，下周六出发，想去西湖、雷峰塔和西溪湿地，想吃片儿川和西湖醋鱼，住武林广场附近，
> 第二天傍晚 18:30 杭州东站的高铁回去。」

## 特点

- **行程走得通**：行程表由确定性排程引擎生成并经独立校验器复核，大模型只负责理解需求和解读结果，
  不会出现闭馆时去参观、饭点排在 40 分钟车程外、赶不上返程车这类问题。
- **真实数据**：地点、评分、人均、营业时间、天气、路网通勤全部来自高德实时接口。
- **分天不折返**：穷举所有分天方案，兼顾通勤最短、每天负荷均衡、营业时段与闭馆日。
- **餐饮双轨**：正餐、特色小吃、夜市分别安排；按绕路距离就近挑店；支持忌口与人均预算。
- **必吃优先**：点名要吃的菜顺路时优先安排，卡片上标明哪一餐满足；实在排不进会说明原因。
- **细节到位**：按出行当天取营业时段（含星期、节假日、季节段），大型景区按最近的门区算通勤并标明从哪个门进出，隔水绕行提示，宜早去 / 夜游时段，
  返程日告别餐提前，用户点名的景点优先保留。
- **分店就近**：只说了品牌（如「先锋书店」）时，按当天路线自动选最顺路的那家分店。
- **经验建议**：预约、门票、进出口等模型常识附在对应景点下，并标明未经核实。
- **多轮修改**：「把雷峰塔换成西溪湿地，其他不变」这类追问只改对应部分。
- **自托管**：一个 Python 进程同时提供接口与网页，会话保存在本地 SQLite；本机、云服务器、Docker 均可部署。
- 桌面与手机浏览器均可使用，界面支持中英切换。

## 技术栈

| 层 | 技术 |
|---|---|
| Agent | OpenAI Agents SDK（Python），任意 OpenAI 兼容模型（需支持 function calling） |
| 服务 | Starlette + uvicorn，SSE 流式输出；SQLite 保存会话与模型记忆 |
| 数据 | 高德 Web 服务 API |
| 排程引擎 | 纯 Python（状态压缩 DP 分天、时间轴模拟、独立校验） |
| 前端 | React 18 + Vite + TypeScript |

## 快速开始

需要 Python 3.11+、Node.js 20+，以及一个高德「Web 服务」Key 和一个 OpenAI 兼容的大模型 Key。

```bash
# 1. 安装依赖
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # macOS / Linux：.venv/bin/python
npm install

# 2. 配置密钥：复制 .env.example 为 .env，填写 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / AMAP_API_KEY

# 3. 构建前端并启动服务
npm run build
./.venv/Scripts/python.exe -m server
```

打开 http://127.0.0.1:8000 即可使用。开发时前端热更新的方式见 [开发指南](docs/DEVELOPMENT.md)。

### Docker

```bash
cp .env.example .env      # 填写密钥
docker compose up -d --build
```

服务监听 8000 端口，数据库保存在 `./data/`。

## 部署

本机、Linux 服务器（systemd + Nginx + HTTPS）、Docker 三种方式，以及环境变量、数据备份、升级与常见问题，
见 [部署指南](docs/DEPLOYMENT.md)。

## 测试

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests -t .   # 不联网、不花钱
npm run build                                                   # 类型检查 + 生产构建
```

`eval/` 提供 11 条真实提示词的端到端测评（会产生调用费用），见 [开发指南](docs/DEVELOPMENT.md#5-真实环境测评)。

## 项目结构

```
app/                   Agent 与业务逻辑（与 Web 框架无关）
  agent.py             Agent 定义、提示词、SSE 事件流
  _tools.py            5 个工具：地理编码、天气、景点检索、餐饮检索、行程生成
  _data/               高德客户端（节流、重试、脱敏）、POI 缓存、路网通勤、参数转换
  engine/              排程引擎：分天、时间轴、营业时间、校验、渲染
server/                HTTP 服务
  app.py               路由：/chat、/chat/stop、会话管理、/health、前端静态文件
  store.py             SQLite：会话列表、聊天记录、模型会话记忆
src/                   React 前端
tests/                 单元、回归与接口测试
eval/                  真实环境测评脚本
docs/                  开发、部署、架构文档
Dockerfile             两阶段构建：前端 + Python 服务
docker-compose.yml
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 环境搭建、本地运行、测试与测评、开发约定、常见问题 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 本机 / 服务器 / Docker 部署、环境变量、HTTPS、备份、升级 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 设计原则、请求链路、接口、存储、工具、排程引擎算法与参数依据、数据源 |
| [docs/API-RESEARCH.md](docs/API-RESEARCH.md) | 数据源调研与实测结果 |

## 已知限制

- 没有登录鉴权：每个浏览器生成一个随机用户 id 区分会话列表。对公网开放时建议在反向代理上加访问控制（见部署指南）。
- 服务按单进程设计（停止生成的信号保存在进程内存里），不要用多 worker 启动。
- 景点超过 10 个时分天退回启发式算法，不保证最优。
- 行程卡片内容固定为中文。
