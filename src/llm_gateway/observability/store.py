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
from typing import Callable, Optional

WINDOW_S = 60
SHORT_WINDOW_S = 10
TTFT_SAMPLES_PER_BUCKET = 64
IDLE_EVICT_S = 120           # a key with nothing in flight and no writes for this long is dropped
MAX_MODELS_PER_SERVER = 200  # model names are client-supplied; overflow shares the "other" key
OVERFLOW_MODEL = "other"

Key = tuple[str, str, str]  # (server, server_type, model)


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
class _KeyState:
    server_type: str
    buckets: list[_Bucket] = field(default_factory=lambda: [_Bucket() for _ in range(WINDOW_S)])
    waiting: int = 0
    generating: int = 0
    inflight_bytes: int = 0
    last_active: float = 0.0


class LiveStats:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[Key, _KeyState] = {}

    # ------------------------------------------------------------------ helpers
    def now(self) -> float:
        return self._clock()

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
    def dispatch(self, key: Key, now: Optional[float] = None) -> None:
        now = self.now() if now is None else now
        with self._lock:
            st = self._state(key)
            st.waiting += 1
            self._bucket(st, now).requests += 1

    def retarget(self, old: Key, new: Key, now: Optional[float] = None) -> None:
        """Move a still-waiting request from one key to another (post-fallback)."""
        if old == new:
            return
        now = self.now() if now is None else now
        with self._lock:
            so = self._state(old)
            so.waiting = max(0, so.waiting - 1)
            bo = self._bucket(so, now)
            bo.requests = max(0, bo.requests - 1)
            sn = self._state(new)
            sn.waiting += 1
            self._bucket(sn, now).requests += 1

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
        decode_seconds: Optional[float] = None,
        ttft: Optional[float] = None,
        estimated: bool = False,
        now: Optional[float] = None,
    ) -> None:
        now = self.now() if now is None else now
        with self._lock:
            st = self._state(key)
            if was_generating:
                st.generating = max(0, st.generating - 1)
            else:
                st.waiting = max(0, st.waiting - 1)
            b = self._bucket(st, now)
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

    def add_inflight_bytes(self, key: Key, delta: int) -> None:
        with self._lock:
            st = self._state(key)
            st.inflight_bytes = max(0, st.inflight_bytes + delta)

    # ------------------------------------------------------------------- reads
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
