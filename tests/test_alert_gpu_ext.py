"""GPU extended metrics -> alert samples -> evaluation (XID, ECC, temp)."""
from llm_gateway.alerts import incidents
from llm_gateway.alerts.engine import AlertSeries, evaluate
from llm_gateway.alerts.rules import default_rules


def _with_series(s, fn):
    orig = incidents._series
    incidents._series = s
    try:
        fn()
    finally:
        incidents._series = orig


def test_gpu_samples_include_xid_and_ecc_when_present():
    s = AlertSeries()

    def run():
        incidents._add_gpu_samples(
            {"index": 0, "mem_used_mib": 100, "mem_total_mib": 1000, "temp_c": 91,
             "xid_error": 1, "ecc_dbe": 3}, 1000.0, host="gpu-a")

    _with_series(s, run)
    assert s.series("gpu_xid", "gpu-a/gpu0") == [(1000.0, 1.0)]
    assert s.series("gpu_ecc_dbe", "gpu-a/gpu0") == [(1000.0, 3.0)]
    assert s.series("gpu_temp_c", "gpu-a/gpu0") == [(1000.0, 91.0)]
    assert s.series("gpu_memory_pct", "gpu-a/gpu0")  # existing logic intact


def test_gpu_samples_skip_missing_xid_and_ecc():
    s = AlertSeries()

    def run():
        incidents._add_gpu_samples(
            {"index": 1, "temp_c": 60, "xid_error": None, "ecc_dbe": None}, 1000.0, host="h")

    _with_series(s, run)
    assert s.targets("gpu_xid") == [] and s.targets("gpu_ecc_dbe") == []
    assert s.series("gpu_temp_c", "h/gpu1")


def test_xid_rule_fires_immediately_without_window():
    rule = next(r for r in default_rules() if r.id == "gpu_xid_error")
    assert rule.window_s == 0  # event-style
    s = AlertSeries()
    s.add("gpu_xid", "gpu-a/gpu0", 1.0, 1000.0)
    findings = evaluate([rule], s, 1000.0)
    assert len(findings) == 1 and not findings[0].resolve
    assert findings[0].rule_id == "gpu_xid_error" and findings[0].value == 1.0
    # Value back to 0 -> recovery finding for the open episode
    s.add("gpu_xid", "gpu-a/gpu0", 0.0, 1030.0)
    rec = evaluate([rule], s, 1030.0, open_firing={"gpu_xid_error": {"gpu-a/gpu0"}})
    assert len(rec) == 1 and rec[0].resolve


def test_zero_xid_never_fires():
    rule = next(r for r in default_rules() if r.id == "gpu_xid_error")
    s = AlertSeries()
    for i in range(5):
        s.add("gpu_xid", "g/gpu0", 0.0, 1000.0 + i * 30)
    assert evaluate([rule], s, 1120.0) == []


def test_ecc_rule_seeded_enabled_and_fires_over_window():
    rule = next(r for r in default_rules() if r.id == "gpu_ecc_dbe_error")
    assert rule.enabled and rule.metric == "gpu_ecc_dbe" and rule.threshold == 1
    s = AlertSeries()
    for i in range(4):
        s.add("gpu_ecc_dbe", "g/gpu0", 2.0, 1000.0 + i * 30)
    findings = evaluate([rule], s, 1090.0)
    assert len(findings) == 1 and findings[0].severity == "critical"


def test_temp_and_xid_rules_now_enabled_in_defaults():
    by_id = {r.id: r for r in default_rules()}
    assert by_id["gpu_temp_high"].enabled
    assert by_id["gpu_xid_error"].enabled


def test_gpu_temp_high_fires_on_hot_gpu():
    rule = next(r for r in default_rules() if r.id == "gpu_temp_high")
    s = AlertSeries()
    for i in range(4):
        s.add("gpu_temp_c", "g/gpu0", 95.0, 1000.0 + i * 30)
    findings = evaluate([rule], s, 1090.0)
    assert len(findings) == 1 and not findings[0].resolve
