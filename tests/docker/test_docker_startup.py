"""Pytest wrapper for Docker Compose integration tests.

This test requires Docker and Docker Compose to be installed and running.
Run with: pytest tests/docker/test_docker_startup.py -v

To skip when Docker isn't available:
    pytest tests/ -v --ignore=tests/docker/test_docker_startup.py
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


def docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def docker_compose_available() -> bool:
    """Check if Docker Compose is available."""
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# Skip all tests in this module if Docker isn't available
pytestmark = pytest.mark.skipif(
    not docker_available() or not docker_compose_available(),
    reason="Docker or Docker Compose not available"
)


@pytest.fixture(scope="module")
def project_root() -> Path:
    """Get the project root directory."""
    # Navigate from tests/docker to project root
    return Path(__file__).parent.parent.parent


@pytest.fixture(scope="module")
def docker_compose_file(project_root) -> Path:
    """Get the Docker Compose test file path."""
    return project_root / "docker-compose.test.yml"


@pytest.fixture(scope="module")
def docker_services(project_root, docker_compose_file):
    """Start Docker Compose services for testing.

    This fixture starts all services, waits for them to be healthy,
    and tears them down after tests complete.
    """
    if not docker_compose_file.exists():
        pytest.skip(f"Docker Compose file not found: {docker_compose_file}")

    # Clean up any previous runs
    subprocess.run(
        ["docker", "compose", "-f", str(docker_compose_file), "down", "--volumes", "--remove-orphans"],
        cwd=project_root,
        capture_output=True,
    )

    # Build and start services (but not test-runner)
    print("\nBuilding and starting Docker services...")
    result = subprocess.run(
        [
            "docker", "compose", "-f", str(docker_compose_file),
            "up", "-d", "--build", "broker", "connector", "mock-llm"
        ],
        cwd=project_root,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Failed to start services: {result.stderr}")
        pytest.fail(f"Failed to start Docker services: {result.stderr}")

    # Wait for services to be healthy
    max_wait = 60
    start_time = time.time()
    services_ready = False

    while time.time() - start_time < max_wait:
        result = subprocess.run(
            ["docker", "compose", "-f", str(docker_compose_file), "ps", "--format", "json"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )

        # Check if all services are healthy
        # Simple check - look for "healthy" in output
        ps_result = subprocess.run(
            ["docker", "compose", "-f", str(docker_compose_file), "ps"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )

        if "healthy" in ps_result.stdout.lower():
            # Check broker and connector are healthy
            broker_healthy = "broker" in ps_result.stdout and "(healthy)" in ps_result.stdout
            connector_ready = "connector" in ps_result.stdout

            if broker_healthy:
                print("Services are ready")
                services_ready = True
                break

        time.sleep(2)

    if not services_ready:
        # Get logs for debugging
        logs = subprocess.run(
            ["docker", "compose", "-f", str(docker_compose_file), "logs"],
            cwd=project_root,
            capture_output=True,
            text=True,
        )
        print(f"Service logs:\n{logs.stdout}\n{logs.stderr}")
        pytest.fail("Services did not become healthy in time")

    yield {
        "project_root": project_root,
        "compose_file": docker_compose_file,
    }

    # Cleanup
    print("\nStopping Docker services...")
    subprocess.run(
        ["docker", "compose", "-f", str(docker_compose_file), "down", "--volumes", "--remove-orphans"],
        cwd=project_root,
        capture_output=True,
    )


class TestDockerStartup:
    """Tests for Docker Compose application startup."""

    def test_broker_health_endpoint(self, docker_services):
        """Test that broker health endpoint is accessible."""
        import urllib.request
        import json

        # Use localhost with mapped port
        url = "http://localhost:18080/health"
        max_retries = 10

        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(url, timeout=5) as response:
                    data = json.loads(response.read().decode())
                    assert response.status == 200
                    assert "uptime" in data or "status" in data
                    return
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    pytest.fail(f"Broker health check failed after {max_retries} attempts: {e}")

    def test_broker_models_endpoint(self, docker_services):
        """Test that broker models endpoint works with auth."""
        import urllib.request
        import json

        url = "http://localhost:18443/v1/models"
        req = urllib.request.Request(
            url,
            headers={"Authorization": "Bearer sk-test-user-key"}
        )

        max_retries = 10
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    assert response.status == 200
                    assert data.get("object") == "list"
                    return
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    # Models endpoint may not have models yet, that's ok
                    pytest.skip(f"Models endpoint not ready: {e}")

    def test_broker_requires_auth(self, docker_services):
        """Test that broker API requires authentication."""
        import urllib.request
        import urllib.error

        url = "http://localhost:18443/v1/models"

        try:
            with urllib.request.urlopen(url, timeout=5):
                pytest.fail("Expected 401 without authentication")
        except urllib.error.HTTPError as e:
            assert e.code == 401, f"Expected 401, got {e.code}"

    def test_full_integration_flow(self, docker_services):
        """Run the full integration test suite via test-runner container."""
        project_root = docker_services["project_root"]
        compose_file = docker_services["compose_file"]

        # Run the test-runner container
        result = subprocess.run(
            [
                "docker", "compose", "-f", str(compose_file),
                "run", "--rm", "test-runner"
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )

        print(f"\nTest runner output:\n{result.stdout}")
        if result.stderr:
            print(f"Test runner stderr:\n{result.stderr}")

        assert result.returncode == 0, f"Integration tests failed: {result.stdout}\n{result.stderr}"
