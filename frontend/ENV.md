# 前端env说明

前端通过 `next.config.js` 里的 rewrites 把 `/api/*` 代理到后端。

生产部署时设置环境变量：
```
NEXT_PUBLIC_API_URL=https://your-backend.railway.app
```

本地开发默认指向 `http://127.0.0.1:8001`，无需额外配置。
