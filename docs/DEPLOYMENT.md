# 部署指南

整个应用是**一个 Python 进程**：`python -m server` 同时提供 Agent 接口、会话管理接口和构建好的网页（`dist/`），
会话与模型记忆保存在一个 SQLite 文件里。不依赖任何云平台，只需要能访问高德接口和大模型接口的网络。

| 方式 | 适合 |
|---|---|
| [本机运行](#3-本机运行) | 自己用、演示 |
| [Linux 服务器 + systemd + Nginx](#4-linux-服务器) | 长期对外提供服务 |
| [Docker](#5-docker) | 已有 Docker 环境，或不想在服务器上装 Python / Node |

---

## 1. 准备密钥

| 环境变量 | 必填 | 用途 | 获取方式 |
|---|---|---|---|
| `AMAP_API_KEY` | 是 | 高德 Web 服务：地点检索、地理编码、天气、路网距离 | [高德开放平台](https://console.amap.com/dev/key/app) → 应用管理 → 创建 Key，**服务平台必须选「Web 服务」** |
| `LLM_API_KEY` | 是 | 大模型调用 | 任意 OpenAI 兼容平台（DeepSeek、通义千问、OpenAI、自建网关等） |
| `LLM_BASE_URL` | 是 | OpenAI 兼容端点，含 `/v1`，如 `https://api.deepseek.com/v1` | 同上 |
| `LLM_MODEL` | 是 | 模型名，如 `deepseek-chat` | 同上 |

注意：

- **模型必须支持 function calling 与流式输出**，否则 Agent 无法调用工具，只会聊天。
- 高德 Key 选错平台类型（如「Web 端 JS API」）会返回 `10009`；平台类型创建后不能修改，只能新建 Key。
- 免费高德 Key 的 QPS 很低，代码里已做全局节流和限流退避重试；用户量大时需要升级配额。
- 兼容旧配置：`LLM_*` 未设置时会读取同名的 `AI_GATEWAY_API_KEY` / `AI_GATEWAY_BASE_URL` / `AI_GATEWAY_MODEL`。

## 2. 其他配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `HOST` | `127.0.0.1` | 监听地址。放在 Nginx 后面保持默认；需要直接对外访问时填 `0.0.0.0` |
| `PORT` | `8000` | 监听端口 |
| `DATABASE_PATH` | `data/travel-agent.db` | SQLite 文件位置，目录不存在会自动创建 |
| `AGENT_MAX_TURNS` | `30` | Agent 单次请求最多轮数 |

所有配置写在项目根目录的 `.env`（复制 `.env.example`），或直接设成进程的环境变量（后者优先）。
`.env` 已在 `.gitignore` 和 `.dockerignore` 中，**不要提交、不要打进镜像**。

启动后访问 `GET /health` 检查配置：

```json
{"status": "ok", "missing": []}
```

`status` 为 `misconfigured` 时，`missing` 列出缺少的变量名（只有名字，不含值）。

---

## 3. 本机运行

需要 Python 3.11+ 与 Node.js 20+。

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # macOS / Linux：.venv/bin/python
npm install
npm run build                    # 生成 dist/
cp .env.example .env             # 填写密钥
./.venv/Scripts/python.exe -m server
```

打开 http://127.0.0.1:8000 。同一局域网的其他设备要访问时，在 `.env` 里设 `HOST=0.0.0.0`，
用 `http://<本机IP>:8000` 打开（Windows 首次会弹防火墙提示，需要允许）。

---

## 4. Linux 服务器

以 Ubuntu 22.04+ / Debian 12 为例，代码放在 `/opt/travel-route-agent`，以普通用户 `travel` 运行。

### 4.1 安装

```bash
sudo apt install -y python3 python3-venv git nginx
# Node.js 只在构建前端时需要；也可以在自己电脑上 npm run build 后把 dist/ 传上去
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash - && sudo apt install -y nodejs

sudo useradd -r -m -d /opt/travel-route-agent travel
sudo -u travel git clone https://github.com/wkxz/travel-router-agent.git /opt/travel-route-agent
cd /opt/travel-route-agent
sudo -u travel python3 -m venv .venv
sudo -u travel .venv/bin/pip install -r requirements.txt
sudo -u travel npm ci && sudo -u travel npm run build

sudo -u travel cp .env.example .env
sudo -u travel chmod 600 .env    # 只有运行用户能读
sudo -u travel nano .env         # 填写密钥
```

### 4.2 systemd

`/etc/systemd/system/travel-agent.service`：

```ini
[Unit]
Description=Travel route agent
After=network-online.target
Wants=network-online.target

[Service]
User=travel
WorkingDirectory=/opt/travel-route-agent
ExecStart=/opt/travel-route-agent/.venv/bin/python -m server
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now travel-agent
sudo systemctl status travel-agent
journalctl -u travel-agent -f    # 查看日志
```

服务监听 `127.0.0.1:8000`，只由本机 Nginx 转发，不直接暴露。

### 4.3 Nginx 反向代理

`/etc/nginx/sites-available/travel-agent`：

```nginx
server {
    listen 80;
    server_name trip.example.com;

    client_max_body_size 1m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE：关闭缓冲，放宽超时（复杂行程 40~90 秒，留足余量）
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/travel-agent /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

服务端已在 `/chat` 响应里带 `X-Accel-Buffering: no`，即使漏了 `proxy_buffering off` 也能逐字推送；
但 `proxy_read_timeout` 必须调大，默认 60 秒会把长请求切断。

### 4.4 HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d trip.example.com
```

### 4.5 访问控制（可选）

应用没有登录功能，任何能打开页面的人都能消耗你的模型与高德额度。只给自己或少数人用时，在 Nginx 上加 Basic Auth：

```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.travel-htpasswd alice
```

在 `location /` 里加：

```nginx
auth_basic "Travel Agent";
auth_basic_user_file /etc/nginx/.travel-htpasswd;
```

`/health` 不需要保护时，可以单独写一个不带 `auth_basic` 的 `location = /health`。

---

## 5. Docker

```bash
cp .env.example .env       # 填写密钥
docker compose up -d --build
docker compose logs -f
```

- 镜像分两阶段构建：Node 构建前端，Python 运行服务；最终镜像不含 Node 与源码之外的文件，也不含 `.env`。
- `.env` 通过 `env_file` 在运行时注入；数据库挂载到宿主机 `./data/`，删除容器不会丢数据。
- 容器内监听 `0.0.0.0:8000`，映射到宿主机 8000 端口；在服务器上建议改成 `127.0.0.1:8000:8000`，再按 [4.3](#43-nginx-反向代理) 用 Nginx 转发。
- 镜像自带健康检查（请求 `/health`），`docker ps` 能看到 healthy / unhealthy。

不用 compose 时：

```bash
docker build -t travel-route-agent .
docker run -d --name travel-agent --env-file .env -p 8000:8000 \
  -v "$PWD/data:/srv/data" --restart unless-stopped travel-route-agent
```

---

## 6. 部署后验证

| 检查项 | 方法 | 预期 |
|---|---|---|
| 配置 | `curl http://127.0.0.1:8000/health` | `{"status": "ok", "missing": []}` |
| 页面 | 浏览器打开站点 | 出现聊天界面与示例问题 |
| Agent | 发送「杭州玩 2 天，想去西湖和灵隐寺，想吃片儿川」 | 工具指示灯依次亮起，40~90 秒内出现行程卡片与文字说明 |
| 流式 | 观察回复 | 文字逐段出现，而不是最后一次性出现（一次性出现说明代理在缓冲） |
| 中断 | 生成中点停止 | 显示「已停止生成」，日志出现 `[stop] ... aborted=True`，之后不再调用工具 |
| 会话 | 刷新页面、切换左侧会话 | 会话列表与历史（含行程卡片）可正常加载 |
| 手机 | 手机浏览器打开 | 侧栏收成顶部一条，输入框在底部 |
| 日志 | `journalctl -u travel-agent` 或 `docker compose logs` | 没有「缺少配置」报错；日志中不出现任何 Key 明文 |

---

## 7. 数据与备份

所有数据都在 `DATABASE_PATH` 指向的 SQLite 文件里（默认 `data/travel-agent.db`，WAL 模式下旁边还有 `-wal`、`-shm` 文件）：

| 表 | 内容 |
|---|---|
| `conversations` / `messages` | 会话列表与聊天记录（助手消息附带行程卡片数据） |
| `agent_sessions` / `agent_messages` | 模型的多轮记忆（由 OpenAI Agents SDK 维护） |

在线备份（服务不用停）：

```bash
sqlite3 data/travel-agent.db ".backup 'backup/travel-agent-$(date +%F).db'"
```

没有 `sqlite3` 命令时，停服务后直接复制整个 `data/` 目录。删除数据库文件即清空所有会话，服务启动时会重建表。

---

## 8. 升级与回退

```bash
cd /opt/travel-route-agent
sudo -u travel git pull
sudo -u travel .venv/bin/pip install -r requirements.txt
sudo -u travel npm ci && sudo -u travel npm run build
sudo systemctl restart travel-agent
```

Docker：`git pull && docker compose up -d --build`。

回退：`git checkout <上一个可用的提交>` 后执行同样的步骤。数据库表结构在启动时以 `CREATE TABLE IF NOT EXISTS` 创建，新旧版本共用同一个数据库文件。

只改了 `.env` 时，重启服务即可生效。

---

## 9. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 打开页面显示「前端尚未构建」 | 没有 `dist/` | 执行 `npm run build` |
| `/health` 返回 `misconfigured` | `.env` 没填全，或不在项目根目录 | 按 `missing` 补齐；systemd 的 `WorkingDirectory` 要指向项目根目录 |
| 回复说地点检索失败，并带高德错误码 | `AMAP_API_KEY` 错误、平台类型不对（10009）或配额用尽 | 检查 Key 与高德控制台配额 |
| 偶发检索失败，错误码 10019~10022 | 高德 QPS 超限 | 已自动退避重试；频繁出现说明配额不足 |
| Agent 只聊天不调用工具 | 模型不支持 function calling | 换用支持工具调用的模型 |
| 回复一次性出现、不是逐字 | 反向代理缓冲了响应 | Nginx 加 `proxy_buffering off`；CDN 需对 `/chat` 关闭缓存与缓冲 |
| 复杂请求中途断开 | 代理超时，或达到 `AGENT_MAX_TURNS` | 调大 `proxy_read_timeout`；适当调大 `AGENT_MAX_TURNS` |
| 点停止没反应 | 用了多个 worker，停止请求落到了另一个进程 | 只用单进程（`python -m server` 默认即单进程） |
| 端口被占用 | 8000 已有其他服务 | `.env` 里改 `PORT` |
| `database is locked` | 多个进程同时写同一个数据库文件 | 同一个数据库只由一个服务进程使用 |
