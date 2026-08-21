#!/usr/bin/env bash
# api.decisionagent.online 切换脚本：分阶段上 conf + certbot 签证书 + 续期 cron + 验证
# 前置：DNSPod 已加 A 记录 api.decisionagent.online → 106.54.246.170
set -e
cd /opt/xiegang

echo "== 0. 备份当前配置 =="
cp nginx/conf.d/xiegang.conf /tmp/xiegang.conf.bak.$(date +%s)

echo "== 1. 阶段1：先上 HTTP-only 配置（ACME 验证路径）=="
mkdir -p nginx/certbot-www
cat > nginx/conf.d/xiegang.conf <<'CONF_STAGE1'
upstream xiegang_web {
    server web:8000;
}

# HTTP - 跳 HTTPS
server {
    listen 80;
    server_name api.liuuuu.xyz _;
    return 301 https://$host$request_uri;
}

# HTTPS
server {
    listen 443 ssl;
    http2 on;
    server_name api.liuuuu.xyz;

    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 10m;

    location / {
        proxy_pass http://xiegang_web;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 关键
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_set_header Connection '';
        proxy_set_header X-Accel-Buffering no;
    }
}


server {
    listen 80;
    server_name api.decisionagent.online;
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}
CONF_STAGE1
docker compose -f docker-compose.prod.yml up -d nginx && echo "阶段1 nginx 已重建（含 certbot 挂载）"

echo "== 2. certbot 签发证书（HTTP-01）=="
mkdir -p letsencrypt
docker run --rm \
  -v /opt/xiegang/nginx/certbot-www:/var/www/certbot \
  -v /opt/xiegang/letsencrypt:/etc/letsencrypt \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d api.decisionagent.online \
  --register-unsafely-without-email --agree-tos --non-interactive

echo "== 3. 证书落位 =="
cp letsencrypt/live/api.decisionagent.online/fullchain.pem nginx/certs/decision-fullchain.pem
cp letsencrypt/live/api.decisionagent.online/privkey.pem   nginx/certs/decision-privkey.pem
ls -la nginx/certs/

echo "== 4. 阶段2：上最终双域名配置 =="
cat > nginx/conf.d/xiegang.conf <<'CONF_FINAL'
upstream xiegang_web {
    server web:8000;
}

# HTTP - 跳 HTTPS
server {
    listen 80;
    server_name api.liuuuu.xyz _;
    return 301 https://$host$request_uri;
}

# HTTPS
server {
    listen 443 ssl;
    http2 on;
    server_name api.liuuuu.xyz;

    ssl_certificate /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 10m;

    location / {
        proxy_pass http://xiegang_web;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 关键
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_set_header Connection '';
        proxy_set_header X-Accel-Buffering no;
    }
}

# ============================================
# api.decisionagent.online（ICP 已备案域名）
# 证书由 certbot webroot 自动签发（scripts/switch-domain.sh），
# cron 每周一自动续期。SSE 配置与 api.liuuuu.xyz 保持一致。
# ============================================
server {
    listen 80;
    server_name api.decisionagent.online;

    # ACME HTTP-01 验证（certbot webroot）
    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    http2 on;
    server_name api.decisionagent.online;

    ssl_certificate /etc/nginx/certs/decision-fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/decision-privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 10m;

    location / {
        proxy_pass http://xiegang_web;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 关键
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_set_header Connection '';
        proxy_set_header X-Accel-Buffering no;
    }
}

CONF_FINAL
docker compose -f docker-compose.prod.yml exec -T nginx nginx -s reload && echo "阶段2 nginx 已重载"

echo "== 5. 续期 cron（每周一凌晨自动 renew + 重载）=="
CRON_LINE="0 3 * * 1 docker run --rm -v /opt/xiegang/nginx/certbot-www:/var/www/certbot -v /opt/xiegang/letsencrypt:/etc/letsencrypt certbot/certbot renew --webroot -w /var/www/certbot --quiet && cp /opt/xiegang/letsencrypt/live/api.decisionagent.online/fullchain.pem /opt/xiegang/nginx/certs/decision-fullchain.pem && cp /opt/xiegang/letsencrypt/live/api.decisionagent.online/privkey.pem /opt/xiegang/nginx/certs/decision-privkey.pem && docker compose -f /opt/xiegang/docker-compose.prod.yml exec -T nginx nginx -s reload"
( crontab -l 2>/dev/null | grep -v "api.decisionagent.online" ; echo "$CRON_LINE" ) | crontab -
crontab -l | tail -2

echo "== 6. 验证 =="
sleep 2
curl -s -o /dev/null -w "https://api.decisionagent.online/api/health → HTTP %{http_code}\n" https://api.decisionagent.online/api/health
echo "完成！"
