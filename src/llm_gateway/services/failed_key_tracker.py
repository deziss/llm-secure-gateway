"""Intra-Provider Key Pool Rotation & Failed Key Tracker.

Tracks unhealthy or exhausted API keys within the same provider pool,
enabling transparent rotation to alternative keys before escalating to
cross-provider fallback chains.
"""

import time
from typing import Dict, List, Optional, Set

# In-memory transient quarantine of failed keys:
# Maps key_identifier -> expiry_timestamp
_quarantined_keys: Dict[str, float] = {}


def record_failed_key(key_identifier: str, cooldown_seconds: float = 60.0) -> None:
    """Quarantine a key that returned 401 Unauthorized or 429 Rate Limit."""
    if key_identifier:
        _quarantined_keys[key_identifier] = time.time() + cooldown_seconds


def is_key_healthy(key_identifier: str) -> bool:
    """Return True if the key is not currently in cooldown."""
    if not key_identifier:
        return True
    expiry = _quarantined_keys.get(key_identifier)
    if not expiry:
        return True
    if time.time() > expiry:
        del _quarantined_keys[key_identifier]
        return True
    return False


def select_healthy_key(keys_pool: List[str]) -> Optional[str]:
    """Select the first healthy key from a pool of candidate keys."""
    if not keys_pool:
        return None
    for k in keys_pool:
        if is_key_healthy(k):
            return k
    # If all are quarantined, return the first one as last resort
    return keys_pool[0]
