# 邪杠会议室 - Dockerfile（Zeabur / Railway / Fly 通用）
# 支持 web 和 worker 两种启动模式
#
# 启动模式由 start_command 区分（zeabur.json 已配好）：
#   web:    uvicorn app.main:app --host 0.0.0.0 --port $PORT
#   worker: python -m app.worker

FROM python:3.13-slim

WORKDIR /app

# 系统依赖（curl 供健康探针用；build-essential 编译 psycopg/cffi）
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖文件（利用 Docker layer 缓存）
COPY backend/pyproject.toml /app/pyproject.toml

# 生产依赖：不装 [dev]（pytest/ruff/httpx 留给本地虚拟环境）
# --no-cache-dir 避免缓存占空间
RUN pip install --no-cache-dir -e .

# 复制全部源码（.dockerignore 已排除 frontend/、node_modules/、.venv/ 等）
COPY backend/ /app/
COPY .env.example /app/.env.example

# 默认端口（Zeabur 会注入 $PORT 覆盖此值）
ENV PORT=8000
EXPOSE 8000

# 默认 web 模式启动；worker service 在 zeabur.json 里覆盖 start_command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
