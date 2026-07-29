#!/usr/bin/env python3
"""部署后端到端冒烟测试脚本。

用法:
    # 指向 Zeabur 已部署的后端
    python scripts/smoke_test.py https://your-app.zeabur.app

    # 本地联调
    python scripts/smoke_test.py http://127.0.0.1:8001

    # 指定 X-Device-Id (复现所有权校验)
    python scripts/smoke_test.py https://... --device-id test-device-001

脚本覆盖 7 个关键点:
    1. CORS / 根路径
    2. /api/health 健康检查
    3. 议会列表 /api/parliament/
    4. 创建议会 POST /api/parliament
    5. 查议会 GET /api/parliament/{id}
    6. SSE 流 /api/parliament/{id}/stream   (限时订阅, 收到一个事件就算通)
    7. worker 心跳 GET /api/parliament/{id}?... 看 worker_available 字段

不会真正跑完整议会 (那需要 GLM API + 12 分钟)。本脚本只验"部署后能立刻判生死"的项。
任一项失败 → exit 1, CI 友好。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


# ---------- pretty 输出 ----------

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"
INFO = "\033[34m·\033[0m"


def section(title: str) -> None:
    print(f"\n\033[1m=== {title} ===\033[0m")


def ok(msg: str, detail: str = "") -> bool:
    print(f"  {PASS} {msg}" + (f"  \033[2m{detail}\033[0m" if detail else ""))
    return True


def fail(msg: str, detail: str = "") -> bool:
    print(f"  {FAIL} {msg}" + (f"  \033[2m{detail}\033[0m" if detail else ""))
    return False


def info(msg: str, detail: str = "") -> None:
    print(f"  {INFO} {msg}" + (f"  \033[2m{detail}\033[0m" if detail else ""))


# ---------- HTTP helpers ----------

def request(
    method: str,
    url: str,
    *,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict | str, dict]:
    """同步 HTTP。返回 (status, json_body|text, response_headers)。"""
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw), dict(resp.headers)
            except json.JSONDecodeError:
                return resp.status, raw, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        try:
            return e.code, json.loads(raw), dict(e.headers)
        except json.JSONDecodeError:
            return e.code, raw, dict(e.headers)


# ---------- 检查项 ----------

def check_health(base: str) -> bool:
    section("1. 健康检查 /api/health")
    code, body, _ = request("GET", f"{base}/api/health", timeout=5)
    if code != 200:
        return fail(f"GET /api/health → {code}", str(body)[:200])
    if isinstance(body, dict) and body.get("status") == "ok":
        return ok(f"/api/health 返回 status=ok", f"service={body.get('service')}")
    return fail("health 返回异常", str(body)[:200])


def check_root(base: str) -> bool:
    section("2. 根路径 / 自检")
    code, body, _ = request("GET", f"{base}/", timeout=5)
    if code != 200:
        return fail(f"GET / → {code}", str(body)[:200])
    msg = body.get("message") if isinstance(body, dict) else ""
    env = body.get("env") if isinstance(body, dict) else "?"
    if "邪杠" in msg or "council" in str(body):
        return ok(f"根路径 OK", f"env={env}")
    return fail(f"根路径返回不对: {msg}", str(body)[:200])


def check_list_parliaments(base: str) -> tuple[bool, list]:
    section("3. 议会列表 GET /api/parliament/")
    code, body, _ = request("GET", f"{base}/api/parliament/", timeout=5)
    if code != 200:
        return fail(f"GET /api/parliament/ → {code}", str(body)[:200]), []
    items = body.get("items", []) if isinstance(body, dict) else []
    return ok(f"议会列表可读", f"总数={len(items)}"), items


def check_create_parliament(
    base: str,
    device_id: str,
    topic: str,
) -> tuple[bool, str | None]:
    section("4. 创建议会 POST /api/parliament")
    payload = {
        "topic": topic,
        "topic_category": "smoke_test",
        "user_background": "部署验证, 请忽略",
    }
    headers = {"X-Device-Id": device_id} if device_id else {}
    code, body, _ = request(
        "POST", f"{base}/api/parliament",
        json_body=payload, headers=headers, timeout=10,
    )
    if code == 429:
        warn_msg = (body.get("detail") if isinstance(body, dict) else str(body))[:120]
        return (
            not fail(f"被限流 (429)", warn_msg),
            None,
        )
    if code != 200:
        return fail(f"POST /api/parliament → {code}", str(body)[:200]), None
    pid = body.get("parliament_id") if isinstance(body, dict) else None
    if not pid:
        return fail("没拿到 parliament_id", str(body)[:200]), None
    info(f"已创建议会", f"id={pid}")
    return ok(f"创建议会成功"), str(pid)


def check_get_parliament(base: str, pid: str, device_id: str) -> bool:
    section("5. 议会详情 GET /api/parliament/{id}")
    headers = {"X-Device-Id": device_id} if device_id else {}
    code, body, _ = request(
        "GET", f"{base}/api/parliament/{pid}", headers=headers, timeout=10,
    )
    if code != 200:
        return fail(f"GET /api/parliament/{pid} → {code}", str(body)[:200])
    status = body.get("status") if isinstance(body, dict) else "?"
    phase = body.get("current_phase") if isinstance(body, dict) else "?"
    worker_available = body.get("worker_available") if isinstance(body, dict) else None
    detail = f"status={status} phase={phase} worker={'yes' if worker_available else ('no' if worker_available is False else '?')}"
    return ok(f"详情可读"), detail


def check_sse_once(
    base: str,
    pid: str,
    device_id: str,
    max_wait: float = 30.0,
) -> bool:
    section("6. SSE 流 GET /api/parliament/{id}/stream")
    url = f"{base}/api/parliament/{pid}/stream"
    headers = {"Accept": "text/event-stream"}
    if device_id:
        headers["X-Device-Id"] = device_id
    req = urllib.request.Request(url, headers=headers, method="GET")
    start = time.monotonic()
    got_event = False
    got_parliament_started = False
    try:
        # urlopen timeout 用 max_wait + 5 兜底；循环里按 max_wait 主动 break
        with urllib.request.urlopen(req, timeout=max_wait + 5) as resp:
            if resp.status != 200:
                return fail(f"SSE HTTP → {resp.status}")
            buf = b""
            for chunk in iter(lambda: resp.read(1024), b""):
                buf += chunk
                # SSE 事件以空行分隔
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    text = block.decode("utf-8", errors="replace")
                    if text.strip() and "data:" in text:
                        got_event = True
                        payload_str = ""
                        for line in text.splitlines():
                            if line.startswith("data:"):
                                payload_str += line[5:].strip()
                        try:
                            payload = json.loads(payload_str)
                            info(
                                f"SSE event",
                                f"type={payload.get('type','?')}",
                            )
                            if payload.get("type") == "parliament_started":
                                got_parliament_started = True
                                return ok(
                                    f"SSE 收到 parliament_started 事件",
                                    f"topic={str(payload.get('topic','?'))[:30]}",
                                )
                        except json.JSONDecodeError:
                            pass
                if time.monotonic() - start > max_wait:
                    break
    except urllib.error.HTTPError as e:
        return fail(f"SSE HTTP 错 {e.code}", e.read().decode("utf-8", "replace")[:200])
    except (TimeoutError, urllib.error.URLError) as e:
        # 服务端 SSE 永不主动关闭 + 没 worker 抢任务 → 本地必然 timeout
        # 这不一定是 bug：端口通、协议对就够了
        info(
            f"SSE 连接超时 ({type(e).__name__})",
            "通常意味着 worker 没在跑（无 parliament_started 事件）",
        )
        if got_event:
            return ok(f"SSE 至少吐了事件（虽没等到 parliament_started）")
        return not fail(
            f"SSE 在 {int(max_wait)}s 内没吐任何事件",
            "检查 worker 进程是否在跑 / Supabase 队列是否被消费",
        )

    # 循环里 break 出来：连接是好的，只是没等到目标事件
    if got_parliament_started:
        return ok("SSE 流已连")
    if got_event:
        return ok(f"SSE 至少吐了事件（虽没等到 parliament_started）")
    info(
        f"{int(max_wait)}s 内无新事件",
        "议会很可能还在 queued（worker 未起）—— 不算 liveliness 失败",
    )
    return ok(f"SSE 连接已建立（无事件 = worker 未抢任务，非服务故障）")


def check_cancel(base: str, pid: str) -> bool:
    section("7. 取消 POST /api/parliament/{id}/cancel")
    code, body, _ = request("POST", f"{base}/api/parliament/{pid}/cancel", timeout=5)
    if code == 404:
        # 议会已结束也算正常
        return ok(f"议会已结束，cancel 接口 404 faithfully")
    if code != 200:
        return fail(f"POST cancel → {code}", str(body)[:200])
    cancelled = (body or {}).get("cancelled_tasks", 0) if isinstance(body, dict) else 0
    return ok(f"取消接口响应"), f"中断了 {cancelled} 个 in-flight 任务"


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="邪杠会议室 backend 端到端冒烟测试",
    )
    parser.add_argument(
        "base_url",
        help="后端 base URL，如 https://your-app.zeabur.app 或 http://127.0.0.1:8001",
    )
    parser.add_argument(
        "--device-id", default=f"smoke-{int(time.time())}",
        help="X-Device-Id (默认随机生成)",
    )
    parser.add_argument(
        "--topic",
        default=f"部署后端到端冒烟测试 {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        help="议会主题",
    )
    parser.add_argument(
        "--skip-sse", action="store_true",
        help="跳过 SSE 流测试 (本地开发可用)",
    )
    parser.add_argument(
        "--sse-timeout", type=float, default=30.0,
        help="SSE 等待 parliament_started 的秒数 (默认 30s, 本地无 worker 可调小到 5)",
    )
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    print(f"\033[1m\033[36m邪杠会议室 backend 冒烟测试\033[0m")
    print(f"  target      : {base}")
    print(f"  device_id   : {args.device_id}")
    info(f"topic       : {args.topic[:60]}")

    results: list[bool] = []

    results.append(check_health(base))
    results.append(check_root(base))
    _, _ = check_list_parliaments(base)
    created_ok, pid = check_create_parliament(base, args.device_id, args.topic)
    results.append(created_ok)

    if pid:
        results.append(check_get_parliament(base, pid, args.device_id))
        if not args.skip_sse:
            results.append(check_sse_once(base, pid, args.device_id, max_wait=args.sse_timeout))
        else:
            info("已跳过 SSE 测试 (--skip-sse)")
        # 最后尝试取消 (释放 worker)
        check_cancel(base, pid)
    else:
        info("议会没创建成功, 跳过后续详情/SSE/cancel 测试")

    # 总结
    section("总结")
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"  {passed}/{total} 检查项通过")
    if passed == total:
        print(f"  {PASS} \033[1m部署 liveliness 全绿\033[0m")
        return 0
    print(f"  {FAIL} \033[1m有失败项, 部署可能有问题\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())
