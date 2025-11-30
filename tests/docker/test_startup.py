#!/usr/bin/env python3
"""Integration test for Docker Compose startup.

This script runs inside the test-runner container and verifies that all
services start correctly and can communicate with each other.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


# Configuration from environment
BROKER_API_URL = os.environ.get("BROKER_API_URL", "http://broker:8443")
BROKER_HEALTH_URL = os.environ.get("BROKER_HEALTH_URL", "http://broker:8080")
CONNECTOR_HEALTH_URL = os.environ.get("CONNECTOR_HEALTH_URL", "http://connector:8081")
USER_API_KEY = os.environ.get("USER_API_KEY", "sk-test-user-key")


def log(message: str) -> None:
    """Print a log message with timestamp."""
    print(f"[TEST] {message}", flush=True)


def http_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Make an HTTP GET request and return status and JSON response."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read().decode()
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return e.code, json.loads(body) if body else {}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to connect to {url}: {e}")


def http_post(url: str, data: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Make an HTTP POST request and return status and JSON response."""
    headers = headers or {}
    headers["Content-Type"] = "application/json"
    body = json.dumps(data).encode()

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            resp_body = response.read().decode()
            return response.status, json.loads(resp_body) if resp_body else {}
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode()
        return e.code, json.loads(resp_body) if resp_body else {}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Failed to connect to {url}: {e}")


def wait_for_service(name: str, url: str, max_retries: int = 30, delay: float = 2.0) -> bool:
    """Wait for a service to become healthy."""
    log(f"Waiting for {name} at {url}...")

    for attempt in range(1, max_retries + 1):
        try:
            status, data = http_get(url)
            if status == 200:
                log(f"{name} is healthy (attempt {attempt})")
                return True
        except ConnectionError as e:
            pass
        except Exception as e:
            log(f"{name} check failed: {e}")

        if attempt < max_retries:
            time.sleep(delay)

    log(f"ERROR: {name} did not become healthy after {max_retries} attempts")
    return False


def test_broker_health() -> bool:
    """Test that broker health endpoint is accessible."""
    log("Testing broker health endpoint...")
    try:
        status, data = http_get(f"{BROKER_HEALTH_URL}/health")
        if status != 200:
            log(f"ERROR: Broker health returned {status}")
            return False
        log(f"Broker health: {data}")
        return True
    except Exception as e:
        log(f"ERROR: Broker health check failed: {e}")
        return False


def test_broker_ready() -> bool:
    """Test that broker ready endpoint shows connectors."""
    log("Testing broker ready endpoint...")
    try:
        status, data = http_get(f"{BROKER_HEALTH_URL}/ready")
        log(f"Broker ready: status={status}, data={data}")
        # Ready should return 200 when connectors are available
        return status == 200
    except Exception as e:
        log(f"ERROR: Broker ready check failed: {e}")
        return False


def test_connector_health() -> bool:
    """Test that connector health endpoint is accessible."""
    log("Testing connector health endpoint...")
    try:
        status, data = http_get(f"{CONNECTOR_HEALTH_URL}/health")
        if status != 200:
            log(f"ERROR: Connector health returned {status}")
            return False
        log(f"Connector health: {data}")
        return True
    except Exception as e:
        log(f"ERROR: Connector health check failed: {e}")
        return False


def test_models_endpoint() -> bool:
    """Test that models endpoint returns available models."""
    log("Testing /v1/models endpoint...")
    try:
        status, data = http_get(
            f"{BROKER_API_URL}/v1/models",
            headers={"Authorization": f"Bearer {USER_API_KEY}"}
        )
        if status != 200:
            log(f"ERROR: Models endpoint returned {status}: {data}")
            return False

        models = data.get("data", [])
        log(f"Available models: {[m.get('id') for m in models]}")

        if not models:
            log("WARNING: No models available yet")
            return False

        return True
    except Exception as e:
        log(f"ERROR: Models endpoint failed: {e}")
        return False


def test_chat_completion() -> bool:
    """Test that chat completion endpoint works."""
    log("Testing /v1/chat/completions endpoint...")
    try:
        status, data = http_post(
            f"{BROKER_API_URL}/v1/chat/completions",
            data={
                "model": "test-model",
                "messages": [
                    {"role": "user", "content": "Hello!"}
                ],
            },
            headers={"Authorization": f"Bearer {USER_API_KEY}"}
        )

        if status != 200:
            log(f"ERROR: Chat completion returned {status}: {data}")
            return False

        # Verify response structure
        if "choices" not in data:
            log(f"ERROR: Response missing 'choices': {data}")
            return False

        choices = data["choices"]
        if not choices:
            log(f"ERROR: Empty choices in response: {data}")
            return False

        message = choices[0].get("message", {})
        content = message.get("content", "")
        log(f"Chat completion response: {content}")

        return True
    except Exception as e:
        log(f"ERROR: Chat completion failed: {e}")
        return False


def test_auth_required() -> bool:
    """Test that authentication is required for API endpoints."""
    log("Testing authentication requirement...")
    try:
        # Try without auth
        status, data = http_get(f"{BROKER_API_URL}/v1/models")
        if status == 401:
            log("Authentication correctly required (401 without token)")
            return True
        else:
            log(f"WARNING: Expected 401 without auth, got {status}")
            return False
    except Exception as e:
        log(f"ERROR: Auth test failed: {e}")
        return False


def test_invalid_model() -> bool:
    """Test that invalid model returns 404."""
    log("Testing invalid model handling...")
    try:
        status, data = http_post(
            f"{BROKER_API_URL}/v1/chat/completions",
            data={
                "model": "nonexistent-model",
                "messages": [{"role": "user", "content": "Hello!"}],
            },
            headers={"Authorization": f"Bearer {USER_API_KEY}"}
        )

        if status == 404:
            log("Invalid model correctly returns 404")
            return True
        else:
            log(f"WARNING: Expected 404 for invalid model, got {status}: {data}")
            return False
    except Exception as e:
        log(f"ERROR: Invalid model test failed: {e}")
        return False


def main() -> int:
    """Run all integration tests."""
    log("=" * 60)
    log("Starting Docker Compose Integration Tests")
    log("=" * 60)

    # Wait for services to be ready
    log("\n--- Waiting for services ---")
    if not wait_for_service("Broker Health", f"{BROKER_HEALTH_URL}/health"):
        return 1

    if not wait_for_service("Connector Health", f"{CONNECTOR_HEALTH_URL}/health"):
        return 1

    # Give connector time to register with broker
    log("Waiting for connector to register with broker...")
    time.sleep(5)

    # Run tests
    tests = [
        ("Broker Health", test_broker_health),
        ("Broker Ready", test_broker_ready),
        ("Connector Health", test_connector_health),
        ("Auth Required", test_auth_required),
        ("Models Endpoint", test_models_endpoint),
        ("Chat Completion", test_chat_completion),
        ("Invalid Model", test_invalid_model),
    ]

    log("\n--- Running Tests ---")
    results = []
    for name, test_func in tests:
        log(f"\n[{name}]")
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            log(f"ERROR: Test {name} raised exception: {e}")
            results.append((name, False))

    # Summary
    log("\n" + "=" * 60)
    log("Test Results Summary")
    log("=" * 60)

    passed = 0
    failed = 0
    for name, result in results:
        status = "PASS" if result else "FAIL"
        log(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1

    log(f"\nTotal: {passed} passed, {failed} failed")
    log("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
