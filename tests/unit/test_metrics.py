import pytest

pytest.importorskip("prometheus_client")

from tasks import metrics  # noqa: E402


def test_no_gateway_configured_is_a_noop(monkeypatch):
    monkeypatch.delenv("PUSHGATEWAY_URL", raising=False)
    assert metrics.push_gauge("pipeline_test_value", "test", 1, table="t") is False


def test_unreachable_gateway_does_not_raise(monkeypatch):
    monkeypatch.setenv("PUSHGATEWAY_URL", "127.0.0.1:9")  # discard port, nothing listens
    assert metrics.push_gauge("pipeline_test_value", "test", 1, table="t") is False
