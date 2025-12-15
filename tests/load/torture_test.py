"""Torture test simulating 5 concurrent Cline users making intensive LLM API calls.

This test simulates realistic Cline usage patterns:
- Rapid-fire requests with tool calls
- Long conversation chains with context buildup
- Mixed streaming and non-streaming requests
- Concurrent sessions from multiple users
- Back-to-back requests without delay

Usage:
    # Against local server
    pytest tests/load/torture_test.py -v -s

    # Against production endpoint
    LLM_API_URL=https://llm.viljo.se/v1 LLM_API_KEY=your-key pytest tests/load/torture_test.py -v -s

    # Run standalone
    python tests/load/torture_test.py --url http://localhost:8443/v1 --api-key sk-test
"""

import argparse
import asyncio
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass, field

import aiohttp
import pytest

# Test configuration
NUM_USERS = 5
REQUESTS_PER_USER = 20
MAX_CONVERSATION_LENGTH = 10
STREAMING_PROBABILITY = 0.7  # 70% of requests are streaming (like Cline)

# Simulated Cline-like prompts
CLINE_PROMPTS = [
    "Read the file src/main.py and explain what it does.",
    "Fix the type error in the function `process_data`.",
    "Write unit tests for the UserService class.",
    "Refactor this code to use async/await patterns.",
    "Add error handling to the database connection code.",
    "Explain why this test is failing and suggest a fix.",
    "Create a new endpoint for user authentication.",
    "Optimize this database query for better performance.",
    "Add logging to the payment processing module.",
    "Review this code for security vulnerabilities.",
    "Implement a caching layer for the API responses.",
    "Debug the memory leak in the background worker.",
    "Add pagination to the list users endpoint.",
    "Write a migration script for the new schema.",
    "Implement rate limiting for the public API.",
]

# Simulated tool calls (like Cline's read_file, write_file, etc.)
TOOL_RESPONSES = [
    "I'll read that file for you. Here's the content:\n```python\ndef main():\n    print('Hello')\n```",
    "I found the issue. The type error is on line 42 where you're passing a string instead of int.",
    "Here are the unit tests I've written:\n```python\nclass TestUserService:\n    def test_create_user(self):\n        pass\n```",
    "I've refactored the code to use async/await. The key changes are...",
    "Added try/except blocks around the database operations with proper logging.",
    "The test is failing because the mock isn't configured correctly. Here's the fix...",
    "Created the new endpoint at /api/v1/auth. Here's the implementation...",
    "Optimized the query by adding an index and using a JOIN instead of subquery.",
    "Added structured logging with correlation IDs throughout the module.",
    "Found 2 potential vulnerabilities: SQL injection risk and missing input validation.",
]


@dataclass
class RequestMetrics:
    """Metrics for a single request."""

    user_id: int
    request_num: int
    start_time: float
    end_time: float
    latency_ms: float
    status_code: int
    streaming: bool
    tokens_received: int
    error: str | None = None


@dataclass
class UserSession:
    """Simulates a Cline user session with conversation history."""

    user_id: int
    messages: list[dict[str, str]] = field(default_factory=list)
    metrics: list[RequestMetrics] = field(default_factory=list)

    def add_system_prompt(self) -> None:
        """Add Cline-like system prompt."""
        self.messages = [
            {
                "role": "system",
                "content": (
                    "You are Cline, an AI coding assistant. You help developers "
                    "write, debug, and improve code. You have access to tools like "
                    "read_file, write_file, execute_command, and search_codebase. "
                    "Be concise and focused on the task at hand."
                ),
            }
        ]

    def add_user_message(self, content: str) -> None:
        """Add a user message to the conversation."""
        self.messages.append({"role": "user", "content": content})

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to the conversation."""
        self.messages.append({"role": "assistant", "content": content})
        # Trim conversation if too long (keep system + last N messages)
        if len(self.messages) > MAX_CONVERSATION_LENGTH + 1:
            self.messages = [self.messages[0]] + self.messages[-(MAX_CONVERSATION_LENGTH):]


@dataclass
class TortureTestResults:
    """Aggregated results from the torture test."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_tokens: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    start_time: float = 0
    end_time: float = 0

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time

    @property
    def requests_per_second(self) -> float:
        if self.duration_seconds > 0:
            return self.total_requests / self.duration_seconds
        return 0

    @property
    def success_rate(self) -> float:
        if self.total_requests > 0:
            return (self.successful_requests / self.total_requests) * 100
        return 0

    @property
    def avg_latency_ms(self) -> float:
        if self.latencies_ms:
            return statistics.mean(self.latencies_ms)
        return 0

    @property
    def p50_latency_ms(self) -> float:
        if self.latencies_ms:
            return statistics.median(self.latencies_ms)
        return 0

    @property
    def p95_latency_ms(self) -> float:
        if len(self.latencies_ms) >= 20:
            sorted_latencies = sorted(self.latencies_ms)
            idx = int(len(sorted_latencies) * 0.95)
            return sorted_latencies[idx]
        return max(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p99_latency_ms(self) -> float:
        if len(self.latencies_ms) >= 100:
            sorted_latencies = sorted(self.latencies_ms)
            idx = int(len(sorted_latencies) * 0.99)
            return sorted_latencies[idx]
        return max(self.latencies_ms) if self.latencies_ms else 0

    def print_summary(self) -> None:
        """Print a summary of the test results."""
        print("\n" + "=" * 60)
        print("TORTURE TEST RESULTS")
        print("=" * 60)
        print(f"Duration:              {self.duration_seconds:.2f}s")
        print(f"Total Requests:        {self.total_requests}")
        print(f"Successful:            {self.successful_requests}")
        print(f"Failed:                {self.failed_requests}")
        print(f"Success Rate:          {self.success_rate:.1f}%")
        print(f"Requests/Second:       {self.requests_per_second:.2f}")
        print(f"Total Tokens:          {self.total_tokens}")
        print("-" * 60)
        print("LATENCY (ms)")
        print(f"  Average:             {self.avg_latency_ms:.1f}")
        print(f"  P50 (Median):        {self.p50_latency_ms:.1f}")
        print(f"  P95:                 {self.p95_latency_ms:.1f}")
        print(f"  P99:                 {self.p99_latency_ms:.1f}")
        if self.latencies_ms:
            print(f"  Min:                 {min(self.latencies_ms):.1f}")
            print(f"  Max:                 {max(self.latencies_ms):.1f}")
        print("=" * 60)

        if self.errors:
            print("\nERRORS (first 10):")
            for error in self.errors[:10]:
                print(f"  - {error}")


class ClineUserSimulator:
    """Simulates a single Cline user making intensive API calls."""

    def __init__(
        self,
        user_id: int,
        base_url: str,
        api_key: str,
        model: str,
        session: aiohttp.ClientSession,
    ):
        self.user_id = user_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.session = session
        self.user_session = UserSession(user_id=user_id)
        self.user_session.add_system_prompt()

    async def make_request(
        self, request_num: int, streaming: bool = False
    ) -> RequestMetrics:
        """Make a single chat completion request."""
        # Pick a random Cline-like prompt
        prompt = random.choice(CLINE_PROMPTS)
        self.user_session.add_user_message(prompt)

        payload = {
            "model": self.model,
            "messages": self.user_session.messages,
            "temperature": 0.7,
            "max_tokens": 500,
            "stream": streaming,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        start_time = time.time()
        status_code = 0
        tokens_received = 0
        error = None
        response_content = ""

        try:
            async with self.session.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                status_code = response.status

                if streaming:
                    # Handle streaming response
                    async for line in response.content:
                        line_str = line.decode("utf-8").strip()
                        if line_str.startswith("data: "):
                            data = line_str[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                if chunk.get("choices"):
                                    delta = chunk["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        response_content += content
                                        tokens_received += 1  # Approximate
                            except json.JSONDecodeError:
                                pass
                else:
                    # Handle non-streaming response
                    data = await response.json()
                    if response.status == 200:
                        if data.get("choices"):
                            response_content = data["choices"][0]["message"]["content"]
                            usage = data.get("usage", {})
                            tokens_received = usage.get("completion_tokens", 0)
                    else:
                        error = data.get("error", {}).get("message", str(data))

        except TimeoutError:
            error = "Request timeout"
            status_code = 504
        except aiohttp.ClientError as e:
            error = f"Client error: {str(e)}"
            status_code = 0
        except Exception as e:
            error = f"Unexpected error: {str(e)}"
            status_code = 0

        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000

        # Add assistant response to conversation history
        if response_content:
            self.user_session.add_assistant_message(response_content)
        elif not error:
            # Simulate a response for conversation continuity
            self.user_session.add_assistant_message(random.choice(TOOL_RESPONSES))

        return RequestMetrics(
            user_id=self.user_id,
            request_num=request_num,
            start_time=start_time,
            end_time=end_time,
            latency_ms=latency_ms,
            status_code=status_code,
            streaming=streaming,
            tokens_received=tokens_received,
            error=error,
        )

    async def run_session(self, num_requests: int) -> list[RequestMetrics]:
        """Run a complete user session with multiple requests."""
        metrics = []

        for i in range(num_requests):
            # Determine if this request should be streaming
            streaming = random.random() < STREAMING_PROBABILITY

            metric = await self.make_request(i, streaming=streaming)
            metrics.append(metric)

            # Log progress
            status = "OK" if metric.status_code == 200 else f"ERR:{metric.status_code}"
            mode = "stream" if streaming else "sync"
            print(
                f"  User {self.user_id} | Req {i + 1}/{num_requests} | "
                f"{mode:6} | {status:8} | {metric.latency_ms:.0f}ms"
            )

            # Small random delay between requests (0-100ms) to simulate human interaction
            # But Cline is often rapid-fire, so keep it short
            await asyncio.sleep(random.uniform(0, 0.1))

        return metrics


async def run_torture_test(
    base_url: str,
    api_key: str,
    model: str,
    num_users: int = NUM_USERS,
    requests_per_user: int = REQUESTS_PER_USER,
) -> TortureTestResults:
    """Run the torture test with multiple concurrent users."""
    print("\nStarting torture test:")
    print(f"  URL: {base_url}")
    print(f"  Model: {model}")
    print(f"  Users: {num_users}")
    print(f"  Requests/User: {requests_per_user}")
    print(f"  Total Requests: {num_users * requests_per_user}")
    print(f"  Streaming Probability: {STREAMING_PROBABILITY * 100}%")
    print()

    results = TortureTestResults()
    results.start_time = time.time()

    # Create a shared session for connection pooling
    connector = aiohttp.TCPConnector(limit=num_users * 2, limit_per_host=num_users * 2)
    async with aiohttp.ClientSession(connector=connector) as session:
        # Create user simulators
        simulators = [
            ClineUserSimulator(
                user_id=i + 1,
                base_url=base_url,
                api_key=api_key,
                model=model,
                session=session,
            )
            for i in range(num_users)
        ]

        # Run all users concurrently
        print("Running concurrent user sessions...")
        tasks = [sim.run_session(requests_per_user) for sim in simulators]
        all_metrics = await asyncio.gather(*tasks)

    results.end_time = time.time()

    # Aggregate results
    for user_metrics in all_metrics:
        for metric in user_metrics:
            results.total_requests += 1
            results.latencies_ms.append(metric.latency_ms)
            results.total_tokens += metric.tokens_received

            if metric.status_code == 200:
                results.successful_requests += 1
            else:
                results.failed_requests += 1
                if metric.error:
                    results.errors.append(
                        f"User {metric.user_id} Req {metric.request_num}: {metric.error}"
                    )

    return results


async def quick_health_check(base_url: str, api_key: str) -> tuple[bool, str]:
    """Quick health check to verify connectivity."""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"Authorization": f"Bearer {api_key}"}
            async with session.get(
                f"{base_url}/models",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    models = [m["id"] for m in data.get("data", [])]
                    return True, f"Available models: {models}"
                else:
                    return False, f"Health check failed: HTTP {response.status}"
    except Exception as e:
        return False, f"Health check failed: {e}"


async def main_async(args: argparse.Namespace) -> int:
    """Async main entry point."""
    # Health check first
    print("Performing health check...")
    healthy, message = await quick_health_check(args.url, args.api_key)
    print(f"  {message}")

    if not healthy:
        print("\nHealth check failed. Aborting test.")
        return 1

    print()

    # Run torture test
    results = await run_torture_test(
        base_url=args.url,
        api_key=args.api_key,
        model=args.model,
        num_users=args.users,
        requests_per_user=args.requests,
    )

    results.print_summary()

    # Return non-zero if too many failures
    if results.success_rate < 90:
        print("\nWARNING: Success rate below 90%!")
        return 1

    return 0


def main() -> int:
    """Main entry point for standalone execution."""
    parser = argparse.ArgumentParser(
        description="Torture test simulating intensive Cline usage"
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("LLM_API_URL", "http://localhost:8443/v1"),
        help="Base URL for the LLM API (default: LLM_API_URL env or localhost:8443)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("LLM_API_KEY", "sk-test"),
        help="API key for authentication (default: LLM_API_KEY env or sk-test)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_MODEL", "gpt-4"),
        help="Model to use for requests (default: LLM_MODEL env or gpt-4)",
    )
    parser.add_argument(
        "--users",
        type=int,
        default=NUM_USERS,
        help=f"Number of concurrent users (default: {NUM_USERS})",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=REQUESTS_PER_USER,
        help=f"Requests per user (default: {REQUESTS_PER_USER})",
    )

    args = parser.parse_args()
    return asyncio.run(main_async(args))


# Pytest integration
@pytest.fixture
def api_config():
    """Get API configuration from environment."""
    return {
        "url": os.environ.get("LLM_API_URL", "http://localhost:8443/v1"),
        "api_key": os.environ.get("LLM_API_KEY", "sk-test"),
        "model": os.environ.get("LLM_MODEL", "gpt-4"),
    }


@pytest.mark.asyncio
async def test_torture_5_concurrent_users(api_config):
    """Torture test with 5 concurrent users making 20 requests each."""
    # Health check
    healthy, message = await quick_health_check(
        api_config["url"], api_config["api_key"]
    )
    if not healthy:
        pytest.skip(f"API not available: {message}")

    results = await run_torture_test(
        base_url=api_config["url"],
        api_key=api_config["api_key"],
        model=api_config["model"],
        num_users=5,
        requests_per_user=20,
    )

    results.print_summary()

    # Assertions
    assert results.success_rate >= 90, f"Success rate {results.success_rate}% < 90%"
    assert results.avg_latency_ms < 60000, f"Avg latency {results.avg_latency_ms}ms > 60s"


@pytest.mark.asyncio
async def test_torture_burst_requests(api_config):
    """Test rapid burst of requests from a single user."""
    healthy, message = await quick_health_check(
        api_config["url"], api_config["api_key"]
    )
    if not healthy:
        pytest.skip(f"API not available: {message}")

    # Single user, rapid fire requests
    results = await run_torture_test(
        base_url=api_config["url"],
        api_key=api_config["api_key"],
        model=api_config["model"],
        num_users=1,
        requests_per_user=50,
    )

    results.print_summary()
    assert results.success_rate >= 85, f"Success rate {results.success_rate}% < 85%"


@pytest.mark.asyncio
async def test_torture_high_concurrency(api_config):
    """Test with higher concurrency (10 users)."""
    healthy, message = await quick_health_check(
        api_config["url"], api_config["api_key"]
    )
    if not healthy:
        pytest.skip(f"API not available: {message}")

    results = await run_torture_test(
        base_url=api_config["url"],
        api_key=api_config["api_key"],
        model=api_config["model"],
        num_users=10,
        requests_per_user=10,
    )

    results.print_summary()
    assert results.success_rate >= 80, f"Success rate {results.success_rate}% < 80%"


if __name__ == "__main__":
    sys.exit(main())
