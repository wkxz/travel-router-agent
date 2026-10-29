# 部署指南

本项目部署在 **腾讯云 EdgeOne Makers**：一个仓库同时包含前端静态站点、Python Agent（OpenAI Agents SDK）与云函数，
平台按 `edgeone.json` 一次构建、统一托管。

> **说明**：本仓库尚未在 EdgeOne 上正式部署过。下面的流程与配置项，在同一技术栈（同一模板、同样的
> `edgeone.json` 结构）的另一个项目上实际部署验证过；本仓库的生产构建 `npm run build` 已在本地验证通过。
> 首次部署后请按「[部署后验证](#5-部署后验证)」逐项核对。

---

## 1. 部署产物与路由

| 组成 | 源码位置 | 部署后路由 | 说明 |
|---|---|---|---|
| 前端 | `src/` → `dist/` | `/` | `npm run build`（`tsc && vite build`）产出静态文件 |
| Agent | `agents/chat/index.py` | `POST /chat` | SSE 流式返回：`text_delta` / `tool_called` / `plan` / `error` / `done` |
| 中断 | `agents/chat/stop.py` | `POST /chat/stop` | 停止正在生成的回复 |
| 云函数 | `cloud-functions/<目录名>/index.py` | `POST /<目录名>` | `conversations`、`history`、`delete-conversation`、`clear-history`；**目录名即路由名** |

平台配置 `edgeone.json`：

```json
{
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "agents": { "timeout": 300, "framework": "openai-agents-sdk", "sandbox": { "timeout": 300 } }
}
```

- Python 依赖：Agent 用根目录 `requirements.txt`，云函数用 `cloud-functions/requirements.txt`。
- Agent 单次请求超时 300 秒。复杂行程（多天、多城市检索）通常 40~90 秒完成。

---

## 2. 准备工作

### 2.1 账号

- 腾讯云账号，已开通 EdgeOne（Makers）。
- GitHub 账号，代码已推送到仓库（EdgeOne 通过绑定 GitHub 仓库拉取代码）。

### 2.2 密钥

| 环境变量 | 必填 | 用途 | 获取方式 |
|---|---|---|---|
| `AMAP_API_KEY` | 是 | 高德 Web 服务：地点检索、地理编码、天气、路网距离 | [高德开放平台](https://console.amap.com/dev/key/app) → 应用管理 → 创建 Key，**服务平台必须选「Web 服务」** |
| `AI_GATEWAY_API_KEY` | 是 | 大模型调用 | EdgeOne 内置模型网关，或任意 OpenAI 兼容平台 |
| `AI_GATEWAY_BASE_URL` | 是 | OpenAI 兼容端点（含 `/v1`） | 同上 |
| `AI_GATEWAY_MODEL` | 否 | 模型名，默认 `@makers/deepseek-v4-flash` | 同上 |
| `AGENT_MAX_TURNS` | 否 | Agent 单次请求最多轮数，默认 `30` | — |

注意：

- 高德 Key 选错平台类型（如「Web 端 JS API」）会返回 `10009`，平台类型创建后不能修改，只能新建 Key。
- 免费高德 Key 的 QPS 很低，代码里已做全局节流和限流退避重试；用户量大时需要升级配额。
- 换用其他模型平台时，**该平台必须支持 function calling 与流式输出**，否则 Agent 无法调用工具。
  EdgeOne 内置网关实测只有 `@makers/deepseek-v4-flash` 可用。

---

## 3. 首次部署

1. **推送代码**：确认要部署的分支（默认 `master`）已推送到 GitHub。
   部署前在本地跑一遍：

   ```bash
   ./.venv/Scripts/python.exe -m unittest discover -s tests -t .
   npm run build
   ```

2. **创建项目**：EdgeOne 控制台 → Makers → 新建项目 → 导入 Git 仓库 → 授权并选择本仓库与分支。
   构建命令、输出目录、Agent 框架会从 `edgeone.json` 读取，无需手填。

3. **配置环境变量**：控制台 → 项目 → 环境变量，按 [2.2](#22-密钥) 填写。
   密钥只配置在控制台，**不要写进代码或提交 `.env`**（`.env` 已在 `.gitignore` 中）。

4. **触发部署**：保存后开始构建，可在控制台查看构建日志；失败时先看日志里的第一条报错。

---

## 4. 访问

- 平台分配的预览域名（`*.edgeone.dev`）**默认开启访问保护**，直接打开会返回 401。
  在控制台点「预览」获取带签名的链接（`?eo_token=...&eo_time=...`，有效期约 3 小时），首次访问后下发 Cookie。
- 需要长期公开访问时，在控制台绑定自定义域名。

---

## 5. 部署后验证

| 检查项 | 方法 | 预期 |
|---|---|---|
| 页面 | 打开站点 | 出现聊天界面与示例问题 |
| Agent | 发送「杭州玩 2 天，想去西湖和灵隐寺，想吃片儿川」 | 工具指示灯依次亮起，40~90 秒内出现行程卡片与文字说明 |
| 中断 | 生成中点停止 | 显示「已停止生成」，不再继续调用工具 |
| 云函数 | 刷新页面、切换左侧会话 | 会话列表与历史记录可正常加载（本地开发时这部分不可用，部署后才有） |
| 手机 | 手机浏览器打开 | 侧栏收成顶部一条，输入框在底部 |
| 日志 | 控制台 → 日志 | 无 `AMAP_API_KEY` / `AI_GATEWAY` 未配置的报错；日志中不应出现任何 Key 明文 |

---

## 6. 更新与回退

- **更新**：仓库与 EdgeOne 绑定后，向部署分支 `git push` 即自动重新构建部署（平台侧构建，仓库里没有也不需要 GitHub Actions）。
- **只改环境变量**：在控制台修改后需重新部署一次才生效。
- **回退**：在控制台的部署记录中重新部署上一个可用版本，或在仓库里 `git revert` 后推送。

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 打开站点返回 401 | 预览域名访问保护 | 用控制台「预览」生成的签名链接，或绑定自定义域名 |
| 页面能打开，会话列表 404 | 前端请求的路由与 `cloud-functions/` 目录名不一致 | 核对 `src/api.ts` 与云函数目录名 |
| 回复说地点检索失败，并带高德错误码 | `AMAP_API_KEY` 未配置、平台类型不对（10009）或配额用尽 | 检查环境变量与高德控制台配额 |
| 偶发检索失败，错误码 10019~10022 | 高德 QPS 超限 | 已自动退避重试；频繁出现说明配额不足 |
| Agent 只聊天不调用工具 | 模型平台不支持 function calling | 换回内置模型或支持工具调用的平台 |
| 复杂请求中途结束 | 达到 `AGENT_MAX_TURNS` 或 300 秒超时 | 适当调大 `AGENT_MAX_TURNS`；超时上限由平台限制 |
| 接入维基百科等境外接口线上不通 | EdgeOne 运行在国内节点，出网与国内网络一致 | 不要依赖被墙的数据源（见 [ARCHITECTURE.md](ARCHITECTURE.md#数据源)） |
