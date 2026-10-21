#!/usr/bin/env bash
# 手工启动 agent-python 本地服务（绕开 edgeone makers dev 在 Windows 上的缺陷）
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1

export PYTHONPATH=".edgeone/agent-python"
export PYTHONUNBUFFERED=1
export AGENT_MODE=dev
export AGENT_TIMEOUT=300
export AGENT_ROUTE_TABLE='{"/chat":{"module":"pages_agents.chat.index","isIndex":true},"/chat/stop":{"module":"pages_agents.chat.stop","isIndex":false}}'

if [ -f "./.venv/Scripts/python.exe" ]; then
    PY="./.venv/Scripts/python.exe"
elif [ -f "./.venv/bin/python" ]; then
    PY="./.venv/bin/python"
else
    PY="python"
fi

$PY -m uvicorn _platform.adapter:app --host 127.0.0.1 --port 8088
