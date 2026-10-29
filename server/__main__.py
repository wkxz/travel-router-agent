"""python -m server：启动 HTTP 服务。监听地址由 HOST / PORT 环境变量决定（默认 127.0.0.1:8000）。"""

import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run("server.app:app", host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")),
                timeout_keep_alive=75)
