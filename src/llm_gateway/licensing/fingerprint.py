import hashlib
import platform
import uuid

def generate_hardware_id() -> str:
    """Generate deterministic SHA-256 fingerprint based on hostname and MAC address."""
    node = hex(uuid.getnode())
    host = platform.node()
    return hashlib.sha256(f"{host}:{node}".encode("utf-8")).hexdigest()

def is_localhost_bypass(hardware_id: str) -> bool:
    """Check if the hardware ID represents a local development / community bypass."""
    return hardware_id.lower() in ("localhost", "127.0.0.1", "::1")
