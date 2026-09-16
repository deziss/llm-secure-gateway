"""Remote Image URL-to-Base64 Inlining for Local & Air-Gapped Inference Backends.

Ensures that multimodal requests sent to local vLLM/Ollama nodes without
direct internet egress can process remote image URLs seamlessly.
"""

import base64
import ipaddress
import logging
import mimetypes
from urllib.parse import urlparse
import httpx

logger = logging.getLogger(__name__)

# Blocked private networks for SSRF protection
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]


def is_ssrf_safe(url: str) -> bool:
    """Validate that the target URL is safe and does not target internal loopback/metadata endpoints."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        if hostname.lower() in ("localhost", "metadata.google.internal"):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    return False
        except ValueError:
            pass  # Standard domain name
        return True
    except Exception:
        return False


async def inline_remote_images(messages: list, timeout_sec: float = 5.0) -> list:
    """Inspect and convert remote image URLs in chat messages to inline Base64 data URIs."""
    if not isinstance(messages, list):
        return messages

    has_remote_image = False
    for msg in messages:
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    img_data = part.get("image_url", {})
                    url = img_data.get("url", "") if isinstance(img_data, dict) else ""
                    if url.startswith(("http://", "https://")):
                        has_remote_image = True
                        break

    if not has_remote_image:
        return messages

    # Clone messages deeply enough to modify image URLs safely
    new_messages = []
    async with httpx.AsyncClient(timeout=timeout_sec, follow_redirects=True) as client:
        for msg in messages:
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                new_messages.append(msg)
                continue

            new_content = []
            for part in msg["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    img_data = part.get("image_url", {})
                    url = img_data.get("url", "") if isinstance(img_data, dict) else ""
                    if url.startswith(("http://", "https://")) and is_ssrf_safe(url):
                        try:
                            resp = await client.get(url)
                            if resp.status_code == 200:
                                content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                                b64 = base64.b64encode(resp.content).decode("ascii")
                                data_uri = f"data:{content_type};base64,{b64}"
                                new_content.append({"type": "image_url", "image_url": {"url": data_uri}})
                                continue
                        except Exception as e:
                            logger.warning("Failed to inline image '%s': %s", url, e)
                new_content.append(part)

            msg_copy = dict(msg)
            msg_copy["content"] = new_content
            new_messages.append(msg_copy)

    return new_messages
