# travel-route-agent

旅游路线生成 + 必吃推荐 Agent（期末自选题）

输入「想去的地方 + 想吃的东西 + 玩几天」→ 输出一份可直接照着走的行程单。

**状态：功能完整，可本地运行；已用 10 条真实提示词做过多轮测评（见 `eval/`）。尚未部署。**

## 核心设计

**LLM 只做意图理解、检索与解读；行程表由确定性 Python 排程引擎生成，并经独立校验器复核。**
模型只能引用工具返回的 POI id，碰不到坐标、评分、营业时间，避免「看起来合理但走不通」的行程。

```
用户自然语言
  → Agent（agents/chat/index.py）：意图解析、并行检索、准备参数
  → 工具层（agents/_tools.py）：高德检索 / 地理编码 / 天气 / 排程
  → 数据层（agents/_data/）：高德客户端（节流、限流重试、脱敏）、POI 仓库（按会话缓存）、路网通勤
  → 排程引擎（agents/engine/）：分天（穷举 + 营业时段模拟）→ 时间轴 → 校验 → 渲染
  → SSE plan 事件直接推给前端（src/components/TripPlanCard.tsx），不经模型转述
```

| 能力 | 实现 |
|---|---|
| 分天不折返 | 景点 ≤10 个时状态压缩 DP 穷举所有分法：通勤最短、负荷均衡、闭馆避让、营业时段走得通 |
| 真实通勤 | 高德 `/v3/distance` 步行/驾车路网（按终点批量，起点 ≤100），取不到时回退估算并标注 |
| 餐饮双轨 | 正餐 / 特色小吃 / 夜市；按「绕路距离」挑店；忌口、人均上限在引擎里过滤 |
| 必吃 | 顺路时优先，绕路上限随剩余机会放宽；排不进时给出原因与补检索建议（gaps / next_step） |
| 时段约束 | 宜早去、夜游（晚餐后窗口）、返程日按车次倒推、博物馆闭馆日解析 |
| 可靠性 | 工具失败返回具体原因；空回复自动重试一次；密钥不出现在任何返回给模型的文本里 |

## 目录

```
agents/
  chat/index.py        Agent 定义、提示词（每次请求注入当天日期）、SSE 流
  _tools.py            5 个工具；generate_trip_itinerary 返回 plan + gaps + next_step
  _data/               amap.py（高德客户端）、poi_repo.py、routing.py（路网）、trip_input.py
  engine/              scheduler / calendar_pack（分天）/ timeline / validator / travel / renderer
cloud-functions/       会话列表、历史、删除（EdgeOne 云函数）
src/                   React 前端
tests/                 单元与回归测试（188 个，不联网）
eval/                  真实环境测评：run_eval.py / analyze.py / compare.py
docs/                  需求分析、数据源调研、实测记录
reference/             参考项目（项目 A）的只读副本，不要修改
```

## 本地运行

Windows 上 `edgeone makers dev` 会卡死，用手工构建 + uvicorn（详见 `HANDOFF.md` §5.3）：

```bash
# 1. 依赖与密钥：.env 里填 AI_GATEWAY_API_KEY / AI_GATEWAY_BASE_URL / AI_GATEWAY_MODEL / AMAP_API_KEY
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2. 构建（找不到 EdgeOne 模板时设置 EDGEONE_AGENT_TEMPLATE；已构建过则只重建业务代码）
./.venv/Scripts/python.exe local-build-agent.py

# 3. 启动 agent（8088）与前端（5173）
bash local-dev.sh
npx vite --config vite.config.local.ts
```

改了 `agents/` 下的代码后要重新执行第 2 步，`.edgeone/` 里是构建副本。

## 测试与测评

```bash
# 单元 / 回归测试（不联网）
./.venv/Scripts/python.exe -m unittest discover -s tests -t .

# 真实环境测评（会产生模型与高德调用费用）
./.venv/Scripts/python.exe eval/run_eval.py eval/results
./.venv/Scripts/python.exe eval/analyze.py eval/results
./.venv/Scripts/python.exe eval/compare.py <旧结果目录> eval/results
```

## 已知限制

- 本地前端代理只转发 `/chat` 与 `/chat/stop`，会话列表 / 历史等云函数接口本地不可用（部署后正常）
- 云函数按前端传来的 `user_id` 区分用户，没有鉴权（沿用模板）
- 景点超过 10 个时分天退回启发式算法，不保证最优
