"""Prometheus pushgateway helpers. Best effort: a push failure never fails a load."""

from __future__ import annotations

import logging
import os
from urllib.error import URLError

log = logging.getLogger(__name__)

JOB = "f1_telemetry_daily"


def push_gauge(name: str, description: str, value: float, **labels: str) -> bool:
    """Set one gauge on the pushgateway, grouped by its labels. Returns success."""
    gateway = os.environ.get("PUSHGATEWAY_URL", "")
    if not gateway:
        log.debug("PUSHGATEWAY_URL not set; skipping metric %s", name)
        return False

    from prometheus_client import CollectorRegistry, Gauge, pushadd_to_gateway

    registry = CollectorRegistry()
    # Labels travel as the grouping key; the gateway attaches them to the series.
    Gauge(name, description, registry=registry).set(value)
    try:
        # pushadd (POST) replaces only this metric name within the group, so
        # different metrics for the same table do not wipe each other out.
        pushadd_to_gateway(gateway, job=JOB, registry=registry, grouping_key=labels)
    except (URLError, OSError) as exc:
        log.warning("Could not push %s to %s: %s", name, gateway, exc)
        return False
    return True
