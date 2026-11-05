"""In-process live statistics per (server, model).

Each key keeps a ring of 60 one-second buckets. Updates are O(1); reads walk at most
60 buckets per key. The clock is injectable so tests can drive time.

Definitions (see ``LiveStats.snapshot``):
  rps_10s / rps_60s        dispatched requests over the window / window length
  waiting                  dispatched, no first byte yet
  generating               first byte received, not finished
  tok_s_decode             mean over requests of output_tokens / (end - first_token), 60 s
  tok_s_throughput         sum of output tokens over 60 s / 60
  ttft_p50_ms              median time-to-first-token over 60 s
  inflight_bytes           bytes of response body currently buffered by in-flight requests
  tokens_estimated         any request in the 60 s window had an estimated token count
"""
from __future__ import annotations

import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

WINDOW_S = 60
SHORT_WINDOW_S = 10
TTFT_SAMPLES_PER_BUCKET = 64
IDLE_EVICT_S = 120           # a key with nothing in flight and no writes for this long is dropped
MAX_MODELS_PER_SERVER = 200  # model names are client-supplied; overflow shares the "other" key
OVERFLOW_MODEL = "other"

Key = tuple[str, str, str]  # (server, server_type, model)


def calculate_percentile(values: list[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    d = k - f
    return s[f] + (s[c] - s[f]) * d


@dataclass
class _Bucket:
    sec: int = -1
    requests: int = 0
    errors: int = 0
    out_tokens: int = 0
    decode_tps_sum: float = 0.0
    decode_n: int = 0
    ttft: list[float] = field(default_factory=list)
    estimated: bool = False

    def reset(self, sec: int) -> None:
        self.sec = sec
        self.requests = 0
        self.errors = 0
        self.out_tokens = 0
        self.decode_tps_sum = 0.0
        self.decode_n = 0
        self.ttft = []
        self.estimated = False


@dataclass
class _MinuteBucket:
    bucket_time: datetime
    server: str
    server_type: str
    model: str
    requests: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    ttft_sum_ms: float = 0.0
    ttft_count: int = 0
    ttft_list: list[float] = field(default_factory=list)
    decode_tps_sum: float = 0.0
    decode_n: int = 0
    itl_sum_ms: float = 0.0
    itl_count: int = 0
    duration_sum_ms: float = 0.0
    waiting_sum: int = 0
    generating_sum: int = 0
    samples_count: int = 0
    server_rps_sum: float = 0.0
    server_tok_s_sum: float = 0.0
    server_running_sum: int = 0
    server_waiting_sum: int = 0
    kv_cache_pct_sum: float = 0.0
    server_scrape_count: int = 0


@dataclass
class _KeyState:
    server_type: str
    buckets: list[_Bucket] = field(default_factory=lambda: [_Bucket() for _ in range(WINDOW_S)])
    waiting: int = 0
    generating: int = 0
    inflight_bytes: int = 0
    last_active: float = 0.0


class LiveStats:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = threading.Lock()
        self._keys: dict[Key, _KeyState] = {}
        self._minute_buckets: dict[tuple[datetime, str, str], _MinuteBucket] = {}

    # ------------------------------------------------------------------ helpers
    def now(self) -> float:
        return self._clock()

    def _bucket_time(self, now: Optional[float | datetime] = None) -> datetime:
        if isinstance(now, datetime):
            if now.tzinfo is not None:
                now = now.astimezone(timezone.utc).replace(tzinfo=None)
            return now.replace(second=0, microsecond=0)
        if self._wall_clock is not None:
            dt = self._wall_clock()
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt.replace(second=0, microsecond=0)
        if now is not None and now > 1_000_000_000:
            return datetime.fromtimestamp(now, tz=timezone.utc).replace(tzinfo=None, second=0, microsecond=0)
        return datetime.now(timezone.utc).replace(tzinfo=None, second=0, microsecond=0)

    def _get_or_create_minute_bucket(self, b_time: datetime, server: str, server_type: str, model: str) -> _MinuteBucket:
        key = (b_time, server, model)
        b = self._minute_buckets.get(key)
        if b is None:
            b = _MinuteBucket(bucket_time=b_time, server=server, server_type=server_type, model=model)
            self._minute_buckets[key] = b
        return b

    def resolve(self, key: Key) -> Key:
        """The key a request is accounted under: ``key``, or the server's "other" bucket
        once that server already tracks MAX_MODELS_PER_SERVER distinct models."""
        with self._lock:
            return self._resolve(key)

    def _resolve(self, key: Key) -> Key:
        if key in self._keys:
            return key
        if sum(1 for k in self._keys if k[0] == key[0]) >= MAX_MODELS_PER_SERVER:
            return (key[0], key[1], OVERFLOW_MODEL)
        return key

    def _evict_idle(self, now: float) -> None:
        for k, st in list(self._keys.items()):
            if (now - st.last_active > IDLE_EVICT_S
                    and not (st.waiting or st.generating or st.inflight_bytes)):
                del self._keys[k]

    def _state(self, key: Key) -> _KeyState:
        st = self._keys.get(key)
        now = self.now()
        if st is None:
            self._evict_idle(now)
            key = self._resolve(key)
            st = self._keys.get(key)
            if st is None:
                st = _KeyState(server_type=key[1])
                self._keys[key] = st
        st.last_active = now
        return st

    @staticmethod
    def _bucket(st: _KeyState, now: float) -> _Bucket:
        sec = int(now)
        b = st.buckets[sec % WINDOW_S]
        if b.sec != sec:
            b.reset(sec)
        return b

    @staticmethod
    def _window(st: _KeyState, now: float, span: int):
        """Buckets whose second falls inside the last ``span`` seconds."""
        cur = int(now)
        cutoff = cur - span  # exclusive
        for b in st.buckets:
            if b.sec > cutoff and b.sec <= cur:
                yield b

    # ------------------------------------------------------------------ writes
    def dispatch(self, key: Key, now: Optional[float | datetime] = None) -> None:
        raw_now = self.now() if now is None or isinstance(now, datetime) else now
        with self._lock:
            st = self._state(key)
            st.waiting += 1
            self._bucket(st, raw_now).requests += 1

            b_time = self._bucket_time(now)
            mb = self._get_or_create_minute_bucket(b_time, key[0], key[1], key[2])
            mb.requests += 1

    def retarget(self, old: Key, new: Key, now: Optional[float | datetime] = None) -> None:
        """Move a still-waiting request from one key to another (post-fallback)."""
        if old == new:
            return
        raw_now = self.now() if now is None or isinstance(now, datetime) else now
        with self._lock:
            so = self._state(old)
            so.waiting = max(0, so.waiting - 1)
            bo = self._bucket(so, raw_now)
            bo.requests = max(0, bo.requests - 1)
            sn = self._state(new)
            sn.waiting += 1
            self._bucket(sn, raw_now).requests += 1

            b_time = self._bucket_time(now)
            mb_old = self._get_or_create_minute_bucket(b_time, old[0], old[1], old[2])
            mb_old.requests = max(0, mb_old.requests - 1)
            mb_new = self._get_or_create_minute_bucket(b_time, new[0], new[1], new[2])
            mb_new.requests += 1

    def first_token(self, key: Key, now: Optional[float] = None) -> None:
        with self._lock:
            st = self._state(key)
            if st.waiting > 0:
                st.waiting -= 1
            st.generating += 1

    def finish(
        self,
        key: Key,
        *,
        was_generating: bool,
        success: bool,
        cancelled: bool = False,
        output_tokens: int = 0,
        prompt_tokens: int = 0,
        total_tokens: int = 0,
        decode_seconds: Optional[float] = None,
        duration_s: Optional[float] = None,
        ttft: Optional[float] = None,
        itl_ms: Optional[float] = None,
        estimated: bool = False,
        now: Optional[float | datetime] = None,
    ) -> None:
        raw_now = self.now() if now is None or isinstance(now, datetime) else now
        with self._lock:
            st = self._state(key)
            if was_generating:
                st.generating = max(0, st.generating - 1)
            else:
                st.waiting = max(0, st.waiting - 1)
            b = self._bucket(st, raw_now)
            if not success and not cancelled:
                b.errors += 1
            if output_tokens > 0:
                b.out_tokens += output_tokens
                if decode_seconds and decode_seconds > 0:
                    b.decode_tps_sum += output_tokens / decode_seconds
                    b.decode_n += 1
            if ttft is not None and ttft >= 0 and len(b.ttft) < TTFT_SAMPLES_PER_BUCKET:
                b.ttft.append(ttft)
            if estimated:
                b.estimated = True

            # Minute bucket aggregation
            b_time = self._bucket_time(now)
            mb = self._get_or_create_minute_bucket(b_time, key[0], key[1], key[2])
            if not success and not cancelled:
                mb.errors += 1
            mb.completion_tokens += output_tokens
            mb.prompt_tokens += prompt_tokens
            mb.total_tokens += (total_tokens if total_tokens else (prompt_tokens + output_tokens))

            if decode_seconds and decode_seconds > 0 and output_tokens > 0:
                mb.decode_tps_sum += output_tokens / decode_seconds
                mb.decode_n += 1

            if ttft is not None and ttft >= 0:
                ttft_ms = ttft * 1000.0
                mb.ttft_sum_ms += ttft_ms
                mb.ttft_count += 1
                mb.ttft_list.append(ttft_ms)

            if itl_ms is not None and itl_ms >= 0:
                mb.itl_sum_ms += itl_ms
                mb.itl_count += 1
            elif decode_seconds and decode_seconds > 0 and output_tokens > 1:
                calc_itl = (decode_seconds / (output_tokens - 1)) * 1000.0
                mb.itl_sum_ms += calc_itl
                mb.itl_count += 1

            if duration_s is not None and duration_s >= 0:
                mb.duration_sum_ms += duration_s * 1000.0
            elif ttft is not None or decode_seconds is not None:
                dur = (ttft or 0.0) + (decode_seconds or 0.0)
                if dur > 0:
                    mb.duration_sum_ms += dur * 1000.0

    def add_inflight_bytes(self, key: Key, delta: int) -> None:
        with self._lock:
            st = self._state(key)
            st.inflight_bytes = max(0, st.inflight_bytes + delta)

    def sample_concurrency(self, now: Optional[float | datetime] = None) -> None:
        with self._lock:
            b_time = self._bucket_time(now)
            for (srv, stype, mdl), st in self._keys.items():
                mb = self._get_or_create_minute_bucket(b_time, srv, stype, mdl)
                mb.waiting_sum += st.waiting
                mb.generating_sum += st.generating
                mb.samples_count += 1

    def record_server_scrape(
        self,
        server: str,
        server_type: str,
        model: str,
        *,
        server_rps: Optional[float] = None,
        server_tok_s: Optional[float] = None,
        server_running: Optional[int] = None,
        server_waiting: Optional[int] = None,
        kv_cache_pct: Optional[float] = None,
        now: Optional[float | datetime] = None,
    ) -> None:
        with self._lock:
            b_time = self._bucket_time(now)
            mb = self._get_or_create_minute_bucket(b_time, server, server_type, model)
            if server_rps is not None:
                mb.server_rps_sum += server_rps
            if server_tok_s is not None:
                mb.server_tok_s_sum += server_tok_s
            if server_running is not None:
                mb.server_running_sum += server_running
            if server_waiting is not None:
                mb.server_waiting_sum += server_waiting
            if kv_cache_pct is not None:
                mb.kv_cache_pct_sum += kv_cache_pct
            mb.server_scrape_count += 1

    # ------------------------------------------------------------------- reads
    def _format_minute_rollups(self, buckets: list[_MinuteBucket]) -> list[dict]:
        out = []
        for b in buckets:
            p50 = round(statistics.median(b.ttft_list), 2) if b.ttft_list else None
            p90 = round(calculate_percentile(b.ttft_list, 0.9), 2) if b.ttft_list else None
            out.append({
                "bucket_time": b.bucket_time,
                "server": b.server,
                "server_type": b.server_type,
                "model": b.model,
                "requests": b.requests,
                "errors": b.errors,
                "prompt_tokens": b.prompt_tokens,
                "completion_tokens": b.completion_tokens,
                "total_tokens": b.total_tokens,
                "ttft_sum_ms": b.ttft_sum_ms,
                "ttft_count": b.ttft_count,
                "ttft_list": list(b.ttft_list),
                "ttft_p50_ms": p50,
                "ttft_p90_ms": p90,
                "decode_tps_sum": b.decode_tps_sum,
                "decode_n": b.decode_n,
                "itl_sum_ms": b.itl_sum_ms,
                "itl_count": b.itl_count,
                "duration_sum_ms": b.duration_sum_ms,
                "waiting_sum": b.waiting_sum,
                "generating_sum": b.generating_sum,
                "samples_count": b.samples_count,
                "server_rps_sum": b.server_rps_sum,
                "server_tok_s_sum": b.server_tok_s_sum,
                "server_running_sum": b.server_running_sum,
                "server_waiting_sum": b.server_waiting_sum,
                "kv_cache_pct_sum": b.kv_cache_pct_sum,
                "server_scrape_count": b.server_scrape_count,
            })
        return out

    def drain_minute_rollups(self) -> list[dict]:
        with self._lock:
            buckets = list(self._minute_buckets.values())
            self._minute_buckets.clear()
        return self._format_minute_rollups(buckets)

    def get_minute_rollups(self) -> list[dict]:
        with self._lock:
            buckets = list(self._minute_buckets.values())
        return self._format_minute_rollups(buckets)

    def get_1s_history(
        self,
        server: Optional[str] = None,
        model: Optional[str] = None,
        now: Optional[float] = None,
    ) -> list[dict]:
        now = self.now() if now is None else now
        cur_sec = int(now)
        out: list[dict] = []
        with self._lock:
            for (k_server, k_server_type, k_model), st in sorted(self._keys.items()):
                if server is not None and k_server != server:
                    continue
                if model is not None and k_model != model:
                    continue

                long_ = list(self._window(st, now, WINDOW_S))
                requests_60 = sum(b.requests for b in long_)
                errors_60 = sum(b.errors for b in long_)
                out_tok = sum(b.out_tokens for b in long_)
                dec_sum = sum(b.decode_tps_sum for b in long_)
                dec_n = sum(b.decode_n for b in long_)
                ttfts = [t for b in long_ for t in b.ttft]

                points = []
                for s in range(cur_sec - (WINDOW_S - 1), cur_sec + 1):
                    b = st.buckets[s % WINDOW_S]
                    if b.sec == s:
                        p_req = b.requests
                        p_err = b.errors
                        p_tok = b.out_tokens
                        p_tps = round(b.decode_tps_sum / b.decode_n, 2) if b.decode_n else 0.0
                        p_p50 = round(statistics.median(b.ttft) * 1000, 1) if b.ttft else None
                        p_p90 = round(calculate_percentile([t * 1000 for t in b.ttft], 0.9), 1) if b.ttft else None
                    else:
                        p_req = 0
                        p_err = 0
                        p_tok = 0
                        p_tps = 0.0
                        p_p50 = None
                        p_p90 = None

                    points.append({
                        "sec_offset": s - cur_sec,
                        "requests": p_req,
                        "errors": p_err,
                        "tokens": p_tok,
                        "rps": float(p_req),
                        "tok_s": p_tps,
                        "ttft_p50_ms": p_p50,
                        "ttft_p90_ms": p_p90,
                    })

                peak_rps = max((p["requests"] for p in points), default=0)
                summary = {
                    "avg_rps": round(requests_60 / WINDOW_S, 3),
                    "peak_rps": float(peak_rps),
                    "total_requests": requests_60,
                    "errors": errors_60,
                    "error_rate_pct": round((errors_60 / requests_60) * 100, 2) if requests_60 else 0.0,
                    "tok_s_decode": round(dec_sum / dec_n, 2) if dec_n else None,
                    "tok_s_throughput": round(out_tok / WINDOW_S, 2),
                    "total_tokens": out_tok,
                    "ttft_p50_ms": round(statistics.median(ttfts) * 1000, 1) if ttfts else None,
                    "ttft_p90_ms": round(calculate_percentile([t * 1000 for t in ttfts], 0.9), 1) if ttfts else None,
                    "itl_ms": None,
                    "server_rps": None,
                    "server_tok_s": None,
                    "server_running_avg": None,
                    "server_waiting_avg": None,
                    "kv_cache_pct_avg": None,
                }
                out.append({
                    "server": k_server,
                    "server_type": k_server_type,
                    "model": k_model,
                    "summary": summary,
                    "series": points,
                    **summary,
                })
        return out

    def snapshot(self, now: Optional[float] = None) -> list[dict]:
        now = self.now() if now is None else now
        out: list[dict] = []
        with self._lock:
            self._evict_idle(now)
            for (server, server_type, model), st in sorted(self._keys.items()):
                short = list(self._window(st, now, SHORT_WINDOW_S))
                long_ = list(self._window(st, now, WINDOW_S))
                requests_60 = sum(b.requests for b in long_)
                errors_60 = sum(b.errors for b in long_)
                out_tok = sum(b.out_tokens for b in long_)
                dec_sum = sum(b.decode_tps_sum for b in long_)
                dec_n = sum(b.decode_n for b in long_)
                ttfts = [t for b in long_ for t in b.ttft]
                requests_10 = sum(b.requests for b in short)
                estimated = any(b.estimated for b in long_)
                active = requests_60 or st.waiting or st.generating or st.inflight_bytes
                if not active:
                    continue
                out.append({
                    "server": server,
                    "server_type": server_type,
                    "model": model,
                    "rps_10s": round(requests_10 / SHORT_WINDOW_S, 3),
                    "rps_60s": round(requests_60 / WINDOW_S, 3),
                    "requests_60s": requests_60,
                    "errors_60s": errors_60,
                    "waiting": st.waiting,
                    "generating": st.generating,
                    "tok_s_decode": round(dec_sum / dec_n, 2) if dec_n else None,
                    "tok_s_throughput": round(out_tok / WINDOW_S, 2),
                    "ttft_p50_ms": round(statistics.median(ttfts) * 1000, 1) if ttfts else None,
                    "inflight_bytes": st.inflight_bytes,
                    "tokens_estimated": estimated,
                })
        return out


_LIVE: LiveStats | None = None
_LIVE_LOCK = threading.Lock()


def get_live_stats() -> LiveStats:
    """Process-wide store used by the proxy hooks and the exporters."""
    global _LIVE
    if _LIVE is None:
        with _LIVE_LOCK:
            if _LIVE is None:
                _LIVE = LiveStats()
    return _LIVE
