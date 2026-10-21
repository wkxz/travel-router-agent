$env:PYTHONPATH=".edgeone/agent-python"
$env:PYTHONUNBUFFERED="1"
$env:PYTHONIOENCODING="utf-8"
$env:AGENT_MODE="dev"
$env:AGENT_TIMEOUT="300"
$env:AGENT_ROUTE_TABLE='{"/chat":{"module":"pages_agents.chat.index","isIndex":true},"/chat/stop":{"module":"pages_agents.chat.stop","isIndex":false}}'

& .\.venv\Scripts\python.exe -m uvicorn _platform.adapter:app --host 127.0.0.1 --port 8088
