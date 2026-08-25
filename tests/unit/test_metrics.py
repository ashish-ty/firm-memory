"""Best-effort infrastructure has to be measurable, or an outage is invisible."""

from firm_memory.metrics import Metrics


def test_counts_calls_failures_and_timeouts_separately():
    """A timeout and a hard failure need different responses."""
    metrics = Metrics()
    metrics.record("search", seconds=0.1)
    metrics.record("search", seconds=0.2, failed=True)
    metrics.record("search", seconds=2.0, failed=True, timed_out=True)

    stats = metrics.snapshot()["search"]
    assert stats["calls"] == 3
    assert stats["failures"] == 2
    assert stats["timeouts"] == 1


def test_latency_is_averaged_across_every_call():
    metrics = Metrics()
    metrics.record("search", seconds=0.1)
    metrics.record("search", seconds=0.3)
    assert metrics.snapshot()["search"]["average_seconds"] == 0.2


def test_operations_are_counted_independently():
    metrics = Metrics()
    metrics.record("search", seconds=0.1)
    metrics.record("insert", seconds=0.1, failed=True)

    snapshot = metrics.snapshot()
    assert snapshot["search"]["failures"] == 0
    assert snapshot["insert"]["failures"] == 1


def test_an_untouched_operation_reports_zeroes_rather_than_failing():
    assert Metrics().for_operation("search").calls == 0


def test_a_snapshot_does_not_alias_the_live_counters():
    metrics = Metrics()
    metrics.record("search", seconds=0.1)
    snapshot = metrics.snapshot()
    metrics.record("search", seconds=0.1)
    assert snapshot["search"]["calls"] == 1
