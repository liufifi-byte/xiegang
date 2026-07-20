"""P0-S1.4 监控模块：议会生命周期 structured log。

MVP 阶段只打 structured log，不接 Sentry。
后续 V3 接 Sentry / Datadog 时换 sink 即可。
"""
import logging
import time

logger = logging.getLogger("xiegang.parliament")


def log_parliament_started(parliament_id: str, device_id: str | None = None, topic: str = "") -> None:
    logger.info(
        "parliament.started id=%s device=%s topic=%r",
        parliament_id, device_id, topic[:80],
    )


def log_parliament_succeeded(parliament_id: str, duration_s: float) -> None:
    logger.info(
        "parliament.succeeded id=%s duration=%.1fs",
        parliament_id, duration_s,
    )


def log_parliament_failed(parliament_id: str, reason: str, duration_s: float = 0.0) -> None:
    logger.warning(
        "parliament.failed id=%s duration=%.1fs reason=%s",
        parliament_id, duration_s, reason[:200],
    )


def log_parliament_cancelled(parliament_id: str) -> None:
    logger.info(
        "parliament.cancelled id=%s",
        parliament_id,
    )


def log_rate_limited(device_id: str, ip: str, reason: str) -> None:
    logger.warning(
        "rate.limited device=%s ip=%s reason=%s",
        device_id, ip, reason,
    )
