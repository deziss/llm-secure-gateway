"""Alert rule customization: filters, validation, CRUD guards, dry-run."""
import pytest

from llm_gateway.alerts.engine import AlertSeries, evaluate
from llm_gateway.alerts.rules import (AlertRule, default_rules, is_default_rule,
                                      make_custom_rule_id, normalize_filters,
                                      target_matches)


def test_target_matches_empty_matches_any():
    assert target_matches([], "anything")
    assert target_matches(None, "anything")


def test_target_matches_glob_and_prefix():
    assert target_matches(["wardha*"], "wardha/gpu0")
    assert target_matches(["wardha"], "wardha/gpu3")          # prefix without wildcard
    assert target_matches(["wardha"], "wardha:llama3")
    assert target_matches(["*:llama3"], "vllm-42:llama3")
    assert not target_matches(["wardha"], "node-b/gpu0")
    assert target_matches(["node-b*", "wardha/gpu7"], "wardha/gpu7")


def test_normalize_filters_trims_dedupes_clamps():
    out = normalize_filters([" wardha* ", "", "wardha*", "x" * 300] + [f"p{i}" for i in range(30)])
    assert out[0] == "wardha*" and out.count("wardha*") == 1
    assert all(len(p) <= 128 for p in out)
    assert len(out) <= 20


def test_default_rule_ids_and_custom_ids():
    for r in default_rules():
        assert is_default_rule(r.id)
    cid = make_custom_rule_id("My Cool Rule!!")
    assert cid.startswith("c-my-cool-rule-") and not is_default_rule(cid)
    assert cid != make_custom_rule_id("My Cool Rule!!")


def _rule(**kw):
    base = dict(id="r1", name="T", metric="gpu_temp_c", comparator=">=", threshold=80.0,
                window_s=0, severity="warning", target_kind="gpu")
    base.update(kw)
    return AlertRule(**base)


def _series_at(metric, targets, value, t):
    s = AlertSeries()
    for tg in targets:
        s.add(metric, tg, value, t)
    return s


def test_target_filter_limits_evaluation():
    now = 1000.0
    s = _series_at("gpu_temp_c", ["wardha/gpu0", "other/gpu0"], 95.0, now)
    unf = evaluate([_rule(target_filter=[])], s, now)
    assert {f.target for f in unf if not f.resolve} == {"wardha/gpu0", "other/gpu0"}
    filt = evaluate([_rule(target_filter=["wardha*"])], s, now)
    assert {f.target for f in filt if not f.resolve} == {"wardha/gpu0"}


def test_open_incident_outside_new_filter_resolves():
    now = 1000.0
    s = _series_at("gpu_temp_c", ["wardha/gpu0"], 95.0, now)
    rule = _rule(target_filter=["other*"])
    out = evaluate([rule], s, now, open_firing={"r1": {"wardha/gpu0"}})
    res = [f for f in out if f.resolve]
    assert len(res) == 1 and res[0].detail["reason"] == "filtered"


@pytest.mark.asyncio
async def test_rule_model_has_new_fields():
    from llm_gateway.models import AlertRule as Row

    r = Row(id="x", name="n", metric="gpu_temp_c",
            target_filter=["wardha*"], description="hello")
    assert r.target_filter == ["wardha*"] and r.description == "hello"
