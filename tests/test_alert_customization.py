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


# ---------------------------------------------------------------------------
# Export / import API
# ---------------------------------------------------------------------------

async def test_export_rules_document_shape(client):
    r = await client.get("/admin/alerts/rules/export")
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    doc = r.json()
    assert doc["format"] == "deziss-alert-rules"
    assert doc["format_version"] == 1
    ids = [x["id"] for x in doc["rules"]]
    assert "gateway_error_rate" in ids
    entry = next(x for x in doc["rules"] if x["id"] == "gateway_error_rate")
    # derived fields must not leak into the portable document
    assert "custom" not in entry and "target_kind" not in entry
    assert set(entry) >= {"id", "name", "metric", "comparator", "threshold",
                          "window_s", "severity", "cooldown_s", "enabled",
                          "channels", "target_filter", "description"}


async def test_import_roundtrip_updates_existing(client):
    exp = await client.get("/admin/alerts/rules/export")
    doc = exp.json()
    doc["rules"][0]["threshold"] = 42.0
    r = await client.post("/admin/alerts/rules/import",
                          json={"format": doc["format"], "format_version": doc["format_version"],
                                "rules": doc["rules"]},
                          headers={"x-csrf-token": "test-csrf-token"})
    assert r.status_code == 200
    out = r.json()
    assert out["created"] == []
    assert out["updated"]  # everything matched by id
    assert not out["errors"]
    exp2 = await client.get("/admin/alerts/rules/export")
    first = next(x for x in exp2.json()["rules"] if x["id"] == doc["rules"][0]["id"])
    assert first["threshold"] == 42.0


async def test_import_creates_custom_and_reports_errors(client):
    body = {"rules": [
        {"name": "Imported Test Rule", "metric": "gpu_temp_c", "comparator": ">=",
         "threshold": 91, "window_s": 120, "severity": "warning", "cooldown_s": 600,
         "channels": ["webhook"], "target_filter": ["wardha*"]},
        {"name": "Bad Metric", "metric": "nope", "comparator": ">=", "threshold": 1,
         "window_s": 60, "severity": "warning"},
        {"name": "No Metric", "comparator": ">=", "threshold": 1, "window_s": 60,
         "severity": "warning"},
    ]}
    r = await client.post("/admin/alerts/rules/import", json=body,
                          headers={"x-csrf-token": "test-csrf-token"})
    assert r.status_code == 200
    out = r.json()
    assert len(out["created"]) == 1 and out["created"][0].startswith("c-")
    names = {e["name"] for e in out["errors"]}
    assert names == {"Bad Metric", "No Metric"}
    # created rule is visible through the normal listing and is deletable
    rules = (await client.get("/admin/alerts/rules")).json()
    ids = [x["id"] for x in rules.get("rules", rules) if isinstance(x, dict)]
    assert out["created"][0] in ids


async def test_import_update_existing_false_skips(client):
    body = {"rules": [{"id": "server_down", "name": "server_down",
                       "metric": "server_error_rate", "comparator": ">=", "threshold": 99,
                       "window_s": 60, "severity": "warning", "cooldown_s": 300}],
            "update_existing": False}
    r = await client.post("/admin/alerts/rules/import", json=body,
                          headers={"x-csrf-token": "test-csrf-token"})
    out = r.json()
    assert out["skipped"] == ["server_down"]
    assert out["created"] == [] and out["updated"] == []


async def test_import_rejects_foreign_format_and_too_many(client):
    r = await client.post("/admin/alerts/rules/import",
                          json={"format": "someone-else", "rules": []},
                          headers={"x-csrf-token": "test-csrf-token"})
    assert r.status_code == 422
    many = {"rules": [{"name": f"r{i}", "metric": "gpu_temp_c", "comparator": ">=",
                       "threshold": 1, "window_s": 60, "severity": "warning"}
                      for i in range(201)]}
    r = await client.post("/admin/alerts/rules/import", json=many,
                          headers={"x-csrf-token": "test-csrf-token"})
    assert r.status_code == 422
