# 邪杠会议室 - Dockerfile（Zeabur / Railway / Fly 通用）
# 支持 web 和 worker 两种启动模式
#
# 启动模式由 start_command 区分（zeabur.json 已配好）：
#   web:    uvicorn app.main:app --host 0.0.0.0 --port $PORT
#   worker: python -m app.worker

FROM python:3.13-slim

WORKDIR /app

# 国内服务器:替换为 USTC Debian 镜像源(deb.debian.org 国内访问极慢会卡死)
RUN set -eux; \
    sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list 2>/dev/null \
    || true; \
    sed -i 's|security.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || true

# 同理替换 pip 源为清华,避免 pip install 卡死
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

# 系统依赖(curl 供健康探针用;build-essential 编译 psycopg/cffi)
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
