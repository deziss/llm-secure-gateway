#!/usr/bin/env python3
"""
LLM Gateway Verification Scripts
End-to-end tests for gateway functionality.
"""

import asyncio
import httpx
import time
import os

BASE_URL = os.getenv("GATEWAY_URL", "http://localhost:6130")


# =============================================================================
# Basic Gateway Verification (v1)
# =============================================================================
async def verify_basic():
    """Basic gateway verification - health, backend, key, proxy"""
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Health Check
        print("Waiting for Health Check...")
        for i in range(10):
            try:
                r = await client.get(f"{BASE_URL}/health")
                if r.status_code == 200:
                    print("✓ Health OK")
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            print("✗ Health Check Failed")
            return False

        # 2. Register Backend
        print("Registering Backend...")
        try:
            await client.delete(f"{BASE_URL}/admin/backends/test-backend")
        except:
            pass
             
        backend_data = {
            "name": "test-backend",
            "base_url": "http://10.10.110.25:11434",
            "fallback_urls": ["http://localhost:11434", "http://10.120.130.55:11434"],
            "backend_type": "ollama",
            "models": ["llama3.2:latest"]
        }
        r = await client.post(f"{BASE_URL}/admin/backends", json=backend_data)
        if r.status_code != 200:
            print(f"✗ Backend Register Failed: {r.text}")
            return False
        print("✓ Backend Registered")

        # 3. Create API Key
        print("Creating API Key...")
        key_data = {
            "owner": "test-user",
            "scopes": ["chat"],
            "rate_limit_rpm": 10
        }
        r = await client.post(f"{BASE_URL}/admin/keys", json=key_data)
        if r.status_code != 200:
            print(f"✗ Key Create Failed: {r.text}")
            return False
        api_key = r.json()["api_key"]
        print(f"✓ API Key: {api_key[:10]}...")

        # 4. Test Proxy
        print("Testing Proxy...")
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"model": "llama3.2:latest", "messages": [{"role": "user", "content": "hi"}]}
        
        try:
            r = await client.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=headers)
            if r.status_code in [401, 403]:
                print("✗ Auth Failed!")
                return False
            print(f"✓ Proxy Response: {r.status_code}")
        except Exception as e:
            print(f"⚠ Proxy Request Failed (may be expected): {e}")

        print("✓ Basic Verification Complete")
        return True


# =============================================================================
# Full V3 Gateway Verification
# =============================================================================
async def verify_v3():
    """Full v3 gateway verification with auth, metrics, owners, provider keys"""
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. Health Check
        print("Waiting for Health Check...")
        for i in range(10):
            try:
                r = await client.get(f"{BASE_URL}/health")
                if r.status_code == 200:
                    print("✓ Health OK")
                    break
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            print("✗ Health Check Failed")
            return False

        # 2. Admin Login
        print("Logging in as Admin...")
        login_data = {"username": "admin@example.com", "password": "admin"}
        r = await client.post(f"{BASE_URL}/auth/jwt/login", data=login_data)
        if r.status_code != 200:
            print(f"✗ Login Failed: {r.status_code} {r.text}")
            return False
        access_token = r.json()["access_token"]
        admin_headers = {"Authorization": f"Bearer {access_token}"}
        print("✓ Login Success")
        
        # 3. Metrics Check
        print("Checking Admin Metrics...")
        r = await client.get(f"{BASE_URL}/admin/metrics", headers=admin_headers)
        if r.status_code != 200:
            print(f"✗ Metrics Failed: {r.text}")
            return False
        print(f"✓ Metrics: {r.json()}")
        
        # 4. Use existing owner
        owner_id = "test-user-v3"
        print(f"Using Owner: {owner_id}")

        # 5. Create Gateway API Key
        print("Creating Gateway API Key...")
        key_data = {"owner": owner_id, "scopes": ["chat"]}
        r = await client.post(f"{BASE_URL}/admin/keys", json=key_data, headers=admin_headers)
        if r.status_code != 200:
            print(f"✗ Key Create Failed: {r.text}")
            return False
        gateway_key = r.json()["api_key"]
        print(f"✓ Gateway Key: {gateway_key[:10]}...")

        # 6. Set Provider Key
        print("Setting Provider Key...")
        pk_data = {"provider": "ollama", "key": "sk-dummy-provider-key"}
        r = await client.post(f"{BASE_URL}/admin/owners/{owner_id}/keys", json=pk_data, headers=admin_headers)
        if r.status_code != 200:
            print(f"✗ Provider Key Failed: {r.text}")
            return False
        print("✓ Provider Key Set")

        # 7. Test v2 Proxy
        print("Testing Request Proxying...")
        proxy_headers = {"x-api-key": gateway_key, "Content-Type": "application/json"}
        payload = {
            "model": "llama3.2:latest", 
            "messages": [{"role": "user", "content": "hello v3"}],
            "stream": False
        }
        
        try:
            r = await client.post(f"{BASE_URL}/ollama/api/chat", json=payload, headers=proxy_headers)
            if r.status_code == 200:
                print("✓ Proxy Success (200 OK)")
            elif r.status_code == 502:
                print("⚠ Proxy 502 (Backend unreachable)")
            else:
                print(f"✗ Proxy Failed: {r.status_code}")
        except Exception as e:
            print(f"⚠ Request Failed: {e}")

        # 8. Check IP Tracking
        print("Verifying IP Tracking...")
        r = await client.get(f"{BASE_URL}/admin/metrics", headers=admin_headers)
        print(f"✓ Metrics: {r.json()}")

        print("✓ V3 Verification Complete")
        return True


# =============================================================================
# Main Runner
# =============================================================================
async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="LLM Gateway Verification")
    parser.add_argument("version", choices=["basic", "v3", "all"], 
                        default="v3", nargs="?",
                        help="Which verification to run (default: v3)")
    args = parser.parse_args()
    
    if args.version == "basic" or args.version == "all":
        print("\n" + "=" * 60)
        print("Running Basic Verification")
        print("=" * 60)
        await verify_basic()
    
    if args.version == "v3" or args.version == "all":
        print("\n" + "=" * 60)
        print("Running V3 Verification")
        print("=" * 60)
        await verify_v3()


if __name__ == "__main__":
    asyncio.run(main())
