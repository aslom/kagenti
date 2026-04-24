"""
OpenShell A2A Connectivity Tests

Tests basic A2A JSON-RPC connectivity and agent card discovery
for all custom A2A agents (weather, ADK, Claude SDK, supervised).

Consolidated from:
- test_weather_agent.py
- test_adk_agent.py::test_hello
- test_claude_sdk_agent.py::test_hello
- test_agent_skills.py::test_agent_card_discovery
- test_agent_skills.py::test_adk_agent_card
- test_skill_discovery.py::test_weather_agent_lists_skills
- test_skill_discovery.py::test_claude_sdk_agent_has_code_review_skill
"""

import os

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import (
    a2a_send,
    extract_a2a_text,
    FIXTURE_MAP,
    LLM_CAPABLE_AGENTS,
    kubectl_run,
)

pytestmark = pytest.mark.openshell

LLM_AVAILABLE = os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() == "true"


def _url(agent: str, request):
    """Get agent URL from fixture map."""
    name = FIXTURE_MAP.get(agent)
    return request.getfixturevalue(name) if name else None


def _deploy_ready(name: str, namespace: str) -> bool:
    """Check if deployment has 1 ready replica."""
    r = kubectl_run(
        "get", "deploy", name, "-n", namespace, "-o", "jsonpath={.status.readyReplicas}"
    )
    return r.returncode == 0 and r.stdout.strip() == "1"


# ═══════════════════════════════════════════════════════════════════════════
# A2A Connectivity (ALL agents)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestA2AConnectivity:
    """Basic A2A JSON-RPC message/send connectivity for all agents."""

    async def test_hello__weather_agent__a2a_response(self, weather_agent_url):
        """Weather agent responds to A2A message/send (no LLM needed)."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(client, weather_agent_url, "Hello, who are you?")
        assert "result" in resp, f"A2A response missing 'result': {resp}"
        text = extract_a2a_text(resp)
        assert text, "Empty response from weather agent"

    async def test_hello__adk_agent__a2a_response(self, adk_agent_url):
        """ADK agent responds to A2A message/send (LLM optional)."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(client, adk_agent_url, "Hello, who are you?")
        assert "result" in resp, f"A2A response missing 'result': {resp}"

    async def test_hello__claude_sdk_agent__a2a_response(self, claude_sdk_agent_url):
        """Claude SDK agent responds to A2A message/send (LLM optional)."""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(client, claude_sdk_agent_url, "Hello, who are you?")
        assert "result" in resp, f"A2A response missing 'result': {resp}"

    async def test_hello__weather_supervised__kubectl_exec(self, agent_namespace):
        """Supervised agent responds via kubectl exec (netns blocks port-forward)."""
        agent = "weather-agent-supervised"
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        r = kubectl_run(
            "exec", f"deploy/{agent}", "-n", agent_namespace, "--", "echo", "alive"
        )
        if r.returncode != 0:
            pytest.skip(f"{agent}: cannot exec into pod — {r.stderr.strip()}")
        assert "alive" in r.stdout


# ═══════════════════════════════════════════════════════════════════════════
# Agent Card Discovery (A2A agents only)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestAgentCardDiscovery:
    """Verify .well-known/agent-card.json is discoverable for all A2A agents."""

    async def test_agent_card__weather_agent__well_known(self, weather_agent_url):
        """Weather agent exposes agent card at .well-known endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{weather_agent_url}/.well-known/agent-card.json", timeout=30.0
            )
        assert resp.status_code == 200
        card = resp.json()
        assert "name" in card

    async def test_agent_card__adk_agent__well_known(self, adk_agent_url):
        """ADK agent exposes agent card at .well-known endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{adk_agent_url}/.well-known/agent.json", timeout=30.0
            )
            if resp.status_code == 404:
                resp = await client.get(
                    f"{adk_agent_url}/.well-known/agent-card.json", timeout=30.0
                )
        assert resp.status_code == 200
        card = resp.json()
        assert "name" in card or "agent" in card

    async def test_agent_card__claude_sdk_agent__well_known(self, claude_sdk_agent_url):
        """Claude SDK agent exposes agent card at .well-known endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{claude_sdk_agent_url}/.well-known/agent-card.json", timeout=30.0
            )
        assert resp.status_code == 200
        card = resp.json()
        assert "name" in card

    async def test_agent_card__weather_supervised__not_accessible(
        self, agent_namespace
    ):
        """Supervised agent netns blocks external HTTP access to .well-known."""
        pytest.skip(
            "weather_supervised: netns blocks external HTTP. "
            "Agent card exists but only accessible via OPA proxy in netns. "
            "TODO: kubectl exec -c agent -- wget http://localhost:8080/.well-known/agent-card.json"
        )
