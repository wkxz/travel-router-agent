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
- **细节到位**：按出行当天取营业时段（含星期、节假日、季节段），隔水绕行提示，宜早去 / 夜游时段，
  返程日告别餐提前，用户点名的景点优先保留。
- **多轮修改**：「把雷峰塔换成西溪湿地，其他不变」这类追问只改对应部分。
- 桌面与手机浏览器均可使用，界面支持中英切换。

## 技术栈

| 层 | 技术 |
|---|---|
| 平台 | 腾讯云 EdgeOne Makers（前端托管 + Python Agent + 云函数） |
| Agent | OpenAI Agents SDK（Python），OpenAI 兼容模型网关 |
| 数据 | 高德 Web 服务 API |
| 排程引擎 | 纯 Python（状态压缩 DP 分天、时间轴模拟、独立校验） |
| 前端 | React 18 + Vite + TypeScript |

## 快速开始

```bash
# 1. 安装依赖
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
npm install

# 2. 配置密钥：复制 .env.example 为 .env，填写 AI_GATEWAY_* 与 AMAP_API_KEY

# 3. 构建并启动 Agent（8088）
./.venv/Scripts/python.exe local-build-agent.py
bash local-dev.sh                  # PowerShell：./local-dev.ps1

# 4. 启动前端（http://localhost:5173）
npx vite --config vite.config.local.ts
```

详细步骤与常见问题见 [开发指南](docs/DEVELOPMENT.md)。

## 部署

推送到 GitHub 后在 EdgeOne Makers 导入仓库，配置 4 个环境变量即可，之后每次推送自动部署。
完整步骤、环境变量说明与部署后验证见 [部署指南](docs/DEPLOYMENT.md)。

## 测试

```bash
./.venv/Scripts/python.exe -m unittest discover -s tests -t .   # 195 个用例，不联网
npm run build                                                   # 类型检查 + 生产构建
```

`eval/` 提供 10 条真实提示词的端到端测评（会产生调用费用），见 [开发指南](docs/DEVELOPMENT.md#5-真实环境测评)。

## 项目结构

```
agents/
  chat/                Agent 定义、提示词、SSE 流（/chat、/chat/stop）
  _tools.py            5 个工具：地理编码、天气、景点检索、餐饮检索、行程生成
  _data/               高德客户端（节流、重试、脱敏）、POI 缓存、路网通勤、参数转换
  engine/              排程引擎：分天、时间轴、营业时间、校验、渲染
cloud-functions/       会话列表、历史、删除
src/                   React 前端
tests/                 单元与回归测试
eval/                  真实环境测评脚本
docs/                  开发、部署、架构文档
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 环境搭建、本地运行、测试与测评、开发约定、常见问题 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | EdgeOne Makers 部署、环境变量、访问与验证、更新与回退 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 设计原则、请求链路、工具、排程引擎算法与参数依据、数据源 |
| [docs/API-RESEARCH.md](docs/API-RESEARCH.md) | 数据源调研与实测结果 |

## 已知限制

- 本地开发时会话列表、历史等云函数接口不可用（部署后正常）。
- 云函数按前端传来的 `user_id` 区分用户，没有登录鉴权。
- 景点超过 10 个时分天退回启发式算法，不保证最优。
- 行程卡片内容固定为中文。
