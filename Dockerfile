# 邪杠会议室 - Dockerfile（Zeabur / Railway / Fly 通用）
# 支持 web 和 worker 两种启动模式

FROM python:3.13-slim

WORKDIR /app

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖文件（利用 Docker 缓存）
COPY backend/pyproject.toml /app/pyproject.toml

# 安装 Python 依赖
RUN pip install --no-cache-dir -e ".[dev]"

# 复制全部源码
COPY backend/ /app/
COPY .env.example /app/.env.example

# 默认端口
ENV PORT=8000
EXPOSE 8000

# 启动命令由 Zeabur 的 service config 覆盖
# 默认 web 模式：
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
