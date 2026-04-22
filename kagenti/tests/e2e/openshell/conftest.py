"""
OpenShell E2E test fixtures.

Provides A2A client helpers, agent URL resolution, and namespace
configuration for the OpenShell PoC agents.

Environment variables:
    OPENSHELL_AGENT_NAMESPACE: Namespace where agents are deployed (default: team1)
    OPENSHELL_GATEWAY_NAMESPACE: Namespace for the gateway (default: openshell-system)
    OPENSHELL_AGENT_PORT: Agent service port (default: 8080)
    OPENSHELL_LLM_AVAILABLE: Set to "true" if an LLM backend is reachable

Run:
    pytest kagenti/tests/e2e/openshell/ -v -m openshell
"""

import json
import os
import subprocess

import httpx
import pytest


# ---------------------------------------------------------------------------
# Custom marker registration
# ---------------------------------------------------------------------------


def pytest_configure(config):
    """Register the openshell marker."""
    config.addinivalue_line(
        "markers",
        "openshell: OpenShell PoC tests (gateway, agents, sandbox lifecycle)",
    )


# ---------------------------------------------------------------------------
# Namespace / environment helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def agent_namespace():
    """Namespace where OpenShell agents are deployed."""
    return os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")


@pytest.fixture(scope="session")
def gateway_namespace():
    """Namespace where the OpenShell gateway runs."""
    return os.getenv("OPENSHELL_GATEWAY_NAMESPACE", "openshell-system")


@pytest.fixture(scope="session")
def agent_port():
    """Port used by agent services (ClusterIP)."""
    return int(os.getenv("OPENSHELL_AGENT_PORT", "8080"))


@pytest.fixture(scope="session")
def llm_available():
    """Whether an LLM backend is available for LLM-dependent tests."""
    return os.getenv("OPENSHELL_LLM_AVAILABLE", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Agent URL helpers
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """Find a free local port for port-forwarding."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _port_forward(name: str, namespace: str, remote_port: int):
    """Start kubectl port-forward and return (local_url, process)."""
    local_port = _find_free_port()
    proc = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            f"svc/{name}",
            f"{local_port}:{remote_port}",
            "-n",
            namespace,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import time

    time.sleep(2)
    return f"http://localhost:{local_port}", proc


@pytest.fixture(scope="session")
def weather_agent_url(agent_namespace, agent_port):
    """Port-forward to weather agent and return local URL."""
    url, proc = _port_forward("weather-agent", agent_namespace, agent_port)
    yield url
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def adk_agent_url(agent_namespace, agent_port):
    """Port-forward to ADK agent and return local URL."""
    url, proc = _port_forward("adk-agent", agent_namespace, agent_port)
    yield url
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def claude_sdk_agent_url(agent_namespace, agent_port):
    """Port-forward to Claude SDK agent and return local URL."""
    url, proc = _port_forward("claude-sdk-agent", agent_namespace, agent_port)
    yield url
    proc.terminate()
    proc.wait()


# ---------------------------------------------------------------------------
# A2A JSON-RPC helper
# ---------------------------------------------------------------------------


async def a2a_send(
    client: httpx.AsyncClient,
    url: str,
    text: str,
    *,
    request_id: str = "test-1",
    timeout: float = 120.0,
) -> dict:
    """Send an A2A ``message/send`` JSON-RPC request and return the parsed response.

    Args:
        client: httpx async client.
        url: Agent A2A endpoint URL.
        text: User message text.
        request_id: JSON-RPC request id.
        timeout: Per-request timeout in seconds.

    Returns:
        Parsed JSON response dict.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": f"msg-{request_id}",
                "parts": [{"type": "text", "text": text}],
            }
        },
    }
    response = await client.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()


def extract_a2a_text(response: dict) -> str:
    """Extract concatenated text from an A2A JSON-RPC response.

    Handles both ``result.artifacts[].parts`` and
    ``result.status.message.parts`` shapes.
    """
    result = response.get("result", {})
    texts: list[str] = []

    # Artifacts — handle both "type" and "kind" field names (A2A spec variants)
    for artifact in result.get("artifacts", []):
        for part in artifact.get("parts", []):
            if part.get("type") == "text" or part.get("kind") == "text":
                texts.append(part.get("text", ""))

    # Status message fallback
    status_msg = result.get("status", {}).get("message", {})
    for part in status_msg.get("parts", []):
        if part.get("type") == "text" or part.get("kind") == "text":
            texts.append(part.get("text", ""))

    return "\n".join(texts)


# ---------------------------------------------------------------------------
# kubectl JSON helper
# ---------------------------------------------------------------------------


def kubectl_get_pods_json(namespace: str) -> list[dict]:
    """Return parsed pod list from ``kubectl get pods -n <ns> -o json``.

    Raises ``pytest.skip`` if kubectl is unavailable or the command fails.
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        pytest.skip("kubectl not found on PATH")

    if result.returncode != 0:
        pytest.skip(
            f"kubectl failed for namespace {namespace}: {result.stderr.strip()}"
        )

    data = json.loads(result.stdout)
    return data.get("items", [])


def kubectl_get_deployments_json(namespace: str) -> list[dict]:
    """Return parsed deployment list from ``kubectl get deployments -n <ns> -o json``."""
    try:
        result = subprocess.run(
            ["kubectl", "get", "deployments", "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        pytest.skip("kubectl not found on PATH")

    if result.returncode != 0:
        pytest.skip(
            f"kubectl failed for namespace {namespace}: {result.stderr.strip()}"
        )

    data = json.loads(result.stdout)
    return data.get("items", [])
