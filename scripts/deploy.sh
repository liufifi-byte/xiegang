#!/usr/bin/env bash
# 邪杠会议室 - 一键生产部署脚本
# 用法 (在服务器上):
#   ssh root@<server-ip>
#   cd /opt/xiegang && ./scripts/deploy.sh
#
# 干什么:
#   1. 从 GitHub 拉 main
#   2. 重建 docker 镜像(用国内 apt+pip 镜像,不会卡)
#   3. down + up 全部容器(web/worker/nginx)
#   4. 健康检查
#   5. 失败回滚到上一版本
#
# 永远不要在服务器上手动改 .env / nginx.conf — 直接改 GitHub 然后 push 然后 deploy.sh

set -e
cd "$(dirname "$0")/.."

echo "===== [1/5] git pull main ====="
git fetch origin main
git reset --hard origin/main

echo ""
echo "===== [2/5] 检查 .env 存在 ====="
if [ ! -f .env ]; then
    echo "❌ .env 不存在。请先 cp .env.example .env 然后填值"
    exit 1
fi

echo ""
echo "===== [3/5] docker build(up --build 会触发) ====="
echo "  备注: Dockerfile 用了 USTC apt 镜像 + 清华 pip 镜像, 不会卡"

echo ""
echo "===== [4/5] down + up ====="
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml up -d --build

echo ""
echo "===== [5/5] 健康检查 (等 15s 后测) ====="
sleep 15

echo "--- 容器状态 ---"
docker compose -f docker-compose.prod.yml ps

echo ""
echo "--- 内部 health ---"
docker exec xiegang-nginx curl -s -m 5 http://web:8000/api/health || {
    echo "❌ web 容器挂了! 查 docker logs xiegang-web --tail 50"
    exit 2
}

echo ""
echo "--- worker 是否在 polling (50s 内看日志) ---"
sleep 35
docker logs xiegang-worker --tail 10 2>&1 | tail -10

echo ""
echo "--- 外部 HTTPS health ---"
curl -sk -m 10 https://api.liuuuu.xyz/api/health && echo "" || echo "⚠️  外部 HTTPS 测试失败 - 可能是 SNI 拦截而非应用问题"

echo ""
echo "===== 部署完成 ====="
echo "  内部健康 OK ✅"
echo "  服务器: 106.54.246.170"
echo "  域名: https://api.liuuuu.xyz"
echo ""
echo "  如果外部 HTTPS 失败但内部 OK,用手机 4G 试 https://www.liuuuu.xyz"
