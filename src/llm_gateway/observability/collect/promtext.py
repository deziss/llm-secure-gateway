"""Tiny Prometheus text-format (exposition 0.0.4) parser.

No third-party dependency. Only what the poller needs: sample lines with labels,
comments skipped, escaped label values, NaN/Inf values. Malformed lines are dropped
rather than failing the whole scrape.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_NAME_RE = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_ESCAPES = {"n": "\n", "\\": "\\", '"': '"'}


@dataclass(frozen=True)
class Sample:
    name: str
    labels: dict = field(default_factory=dict)
    value: float = 0.0


def _parse_value(text: str) -> float:
    low = text.strip().lower()
    if low in ("nan", "+nan", "-nan"):
        return math.nan
    if low in ("+inf", "inf"):
        return math.inf
    if low == "-inf":
        return -math.inf
    return float(low)


def _parse_labels(line: str, i: int) -> tuple[dict, int]:
    """Parse `{k="v",...}` starting at line[i] == '{'. Returns (labels, index after '}')."""
    labels: dict[str, str] = {}
    n = len(line)
    i += 1
    while i < n:
        while i < n and line[i] in " \t,":
            i += 1
        if i < n and line[i] == "}":
            return labels, i + 1
        j = i
        while j < n and line[j] not in "=}":
            j += 1
        if j >= n or line[j] != "=":
            raise ValueError("bad label set")
        key = line[i:j].strip()
        j += 1
        if j >= n or line[j] != '"':
            raise ValueError("label value not quoted")
        j += 1
        buf: list[str] = []
        while j < n and line[j] != '"':
            if line[j] == "\\" and j + 1 < n:
                buf.append(_ESCAPES.get(line[j + 1], line[j + 1]))
                j += 2
            else:
                buf.append(line[j])
                j += 1
        if j >= n:
            raise ValueError("unterminated label value")
        labels[key] = "".join(buf)
        i = j + 1
    raise ValueError("unterminated label set")


def parse(text: str) -> list[Sample]:
    """Parse an exposition-format body into samples. Timestamps are ignored."""
    out: list[Sample] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            m = _NAME_RE.match(line)
            if not m:
                continue
            name = m.group(0)
            i = m.end()
            labels: dict[str, str] = {}
            if i < len(line) and line[i] == "{":
                labels, i = _parse_labels(line, i)
            parts = line[i:].split()
            if not parts:
                continue
            out.append(Sample(name, labels, _parse_value(parts[0])))
        except (ValueError, IndexError):
            continue
    return out


def select(samples: list[Sample], name: str, **match: str) -> list[Sample]:
    """All samples with this exact metric name whose labels contain `match`."""
    return [s for s in samples if s.name == name and all(s.labels.get(k) == v for k, v in match.items())]


def total(samples: list[Sample], *names: str, **match: str) -> float | None:
    """Sum over the first metric name that has any matching sample (None if none).

    Use several names for renamed metrics, e.g. vllm:kv_cache_usage_perc (new)
    and vllm:gpu_cache_usage_perc (old).
    """
    for name in names:
        hits = select(samples, name, **match)
        if hits:
            vals = [h.value for h in hits if not math.isnan(h.value)]
            return sum(vals) if vals else None
    return None


def by_device(samples: list[Sample], id_label: str, metrics: dict[str, str]) -> dict[str, dict]:
    """Group per-device metrics. `metrics` maps a short key to a metric name.

    Returns {device_id: {key: value, ..., "labels": {label: value}}}; the labels of the
    first sample seen for that device are kept (used for uuid / name / model labels).
    """
    wanted = {v: k for k, v in metrics.items()}
    out: dict[str, dict] = {}
    for s in samples:
        key = wanted.get(s.name)
        dev = s.labels.get(id_label)
        if dev is None:
            continue
        entry = out.setdefault(dev, {"labels": {}})
        if key is not None:
            entry[key] = s.value
        for lk, lv in s.labels.items():
            entry["labels"].setdefault(lk, lv)
    return out
