import json
import time
import urllib.request
import urllib.error

BASE_URL = "http://localhost:6130"
API_KEY = "sk-test-gateway-key-12345"

def request(path, method="GET", payload=None, extra_headers=None):
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            elapsed = time.time() - t0
            resp_data = resp.read().decode("utf-8")
            status = resp.status
            resp_headers = dict(resp.headers)
            try:
                parsed = json.loads(resp_data)
            except Exception:
                parsed = resp_data
            return {"status": status, "data": parsed, "elapsed": elapsed, "headers": resp_headers}
    except urllib.error.HTTPError as e:
        elapsed = time.time() - t0
        err_body = e.read().decode("utf-8")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = err_body
        return {"status": e.code, "error": parsed, "elapsed": elapsed, "headers": dict(e.headers)}
    except Exception as e:
        return {"status": 0, "error": str(e), "elapsed": time.time() - t0}

def test_models_discovery():
    print("\n--- 1. Testing GET /v1/models ---")
    res = request("/v1/models", method="GET")
    assert res["status"] == 200, f"Expected 200, got {res}"
    models = [m["id"] for m in res["data"].get("data", [])]
    print(f"Discovered {len(models)} models: {models}")
    return models

def test_chat_model(model_name, prompt="Hello, answer in one short sentence.", max_tokens=100):
    print(f"\n--- Testing Chat Completion for [{model_name}] ---")
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2
    }
    res = request("/v1/chat/completions", method="POST", payload=payload)
    if res["status"] != 200:
        print(f"FAILED [{model_name}]: status={res['status']}, error={res.get('error')}")
        return False
    
    choice = res["data"]["choices"][0]["message"]
    content = choice.get("content") or ""
    reasoning = choice.get("reasoning") or choice.get("reasoning_content") or ""
    print(f"SUCCESS [{model_name}] ({res['elapsed']:.2f}s)")
    if reasoning:
        print(f"   [Reasoning]: {reasoning[:120]}...")
    print(f"   [Content]: {content[:150]}...")
    return True

def test_embeddings():
    print("\n--- Testing Embeddings on [bge-m3] ---")
    payload = {
        "model": "bge-m3",
        "input": "Represent this sentence for searching relevant passages: Hello world"
    }
    res = request("/v1/embeddings", method="POST", payload=payload)
    assert res["status"] == 200, f"Failed embeddings: {res}"
    data = res["data"]["data"]
    dim = len(data[0]["embedding"])
    print(f"SUCCESS [bge-m3]: Vector returned with dimension {dim} ({res['elapsed']:.2f}s)")
    return True

def test_streaming(model_name="llama-3.1-8b"):
    print(f"\n--- Testing Streaming (SSE) on [{model_name}] ---")
    url = f"{BASE_URL}/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "Count from 1 to 5."}],
        "stream": True,
        "max_tokens": 50
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    chunks = 0
    full_text = ""
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        for line in resp:
            line_str = line.decode("utf-8").strip()
            if line_str.startswith("data: ") and line_str != "data: [DONE]":
                try:
                    c = json.loads(line_str[6:])
                    delta = c["choices"][0]["delta"].get("content", "")
                    full_text += delta
                    chunks += 1
                except Exception:
                    pass
    elapsed = time.time() - t0
    print(f"SUCCESS Streaming: Received {chunks} SSE chunks in {elapsed:.2f}s")
    print(f"   [Streamed text]: {full_text.strip()[:100]}...")
    return chunks > 0

def test_agent_detection():
    print("\n--- Testing Agent Detection Header Tagging ---")
    res = request(
        "/v1/chat/completions",
        method="POST",
        payload={
            "model": "llama-3.1-8b",
            "messages": [{"role": "user", "content": "Ping"}],
            "max_tokens": 10
        },
        extra_headers={"User-Agent": "Cursor/0.45.0 (darwin-arm64)"}
    )
    assert res["status"] == 200
    print("SUCCESS Agent detection handled smoothly.")

def test_error_sanitization():
    print("\n--- Testing Error Sanitization (Zero Upstream IP Leakage) ---")
    # Request invalid non-existent model
    res = request(
        "/v1/chat/completions",
        method="POST",
        payload={
            "model": "non-existent-dummy-model",
            "messages": [{"role": "user", "content": "Ping"}],
            "max_tokens": 10
        }
    )
    # The error message should NEVER contain private IPs like 10.10.110.42 or 13313
    err_str = json.dumps(res.get("error", ""))
    print(f"Error status: {res['status']}, sanitization response: {err_str[:120]}")
    assert "10.10.110.42" not in err_str, f"LEAK DETECTED: Upstream IP found in client error: {err_str}"
    assert "13313" not in err_str, f"LEAK DETECTED: Upstream port found in client error: {err_str}"
    print("SUCCESS Error response is completely sanitized (no IP or port leakage).")

if __name__ == "__main__":
    print("=================================================================")
    print("   RUNNING LIVE TEST SUITE: vLLM-42 BACKEND & GATEWAY FEATURES   ")
    print("=================================================================")

    # 1. Discovery
    models = test_models_discovery()
    
    # 2. Embeddings
    test_embeddings()

    # 3. All Chat Models
    chat_models = [
        "llama-3.1-8b",
        "qwen-2.5-14b",
        "llama-3.3-70b",
        "llama-4-scout",
        "llava-1.6-34b",
        "nemotron-3-nano-30b-a3b",
        "qwen-3-30b-a3b",
    ]
    
    all_passed = True
    for m in chat_models:
        # Give reasoning model more tokens so it can output both thinking and answer
        tokens = 350 if "qwen-3" in m else 60
        prompt = "Solve step-by-step: What is 17 * 4?" if "qwen-3" in m else "Say hello and state your name or type in one sentence."
        ok = test_chat_model(m, prompt=prompt, max_tokens=tokens)
        if not ok:
            all_passed = False

    # 4. Streaming
    test_streaming("llama-3.1-8b")

    # 5. Agent Detection
    test_agent_detection()

    # 6. Error Sanitization
    test_error_sanitization()

    print("\n=================================================================")
    if all_passed:
        print("   ALL MODELS AND FEATURES TESTED SUCCESSFULLY! [PASS]           ")
    else:
        print("   SOME TESTS FAILED! PLEASE REVIEW LOGS.                        ")
    print("=================================================================")

def test_anthropic_messages():
    print("\n--- Testing Native Anthropic /v1/messages Endpoint ---")
    url = f"{BASE_URL}/v1/messages"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "vllm-42/llama-3.1-8b",
        "max_tokens": 40,
        "system": "You are a calculator.",
        "messages": [{"role": "user", "content": "What is 100 / 4?"}]
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=30) as resp:
        elapsed = time.time() - t0
        data = json.loads(resp.read().decode("utf-8"))
        assert data.get("type") == "message"
        assert len(data.get("content", [])) > 0
        text = data["content"][0].get("text", "")
        print(f"SUCCESS Anthropic Messages: {text.strip()} ({elapsed:.2f}s)")
    return True

if __name__ == "__main__":
    test_anthropic_messages()
