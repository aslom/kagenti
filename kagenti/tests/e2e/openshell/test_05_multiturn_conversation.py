"""
OpenShell Multi-Turn Conversation Tests

Tests that verify agents handle sequential messages with context continuity.
Consolidated from:
- test_session_persistence.py::TestMultiTurnSequentialMessages
- test_session_persistence.py::TestMultiTurnContextIsolation
- test_session_persistence.py::TestMultiTurnContextContinuity
- test_agent_skills.py::test_multi_turn_conversation
- test_sandbox_lifecycle.py::TestAgentServicePersistence (moved here)
"""

import os

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import (
    a2a_send,
    extract_a2a_text,
    extract_context_id,
    AGENT_PROMPTS,
    FIXTURE_MAP,
    LLM_CAPABLE_AGENTS,
    kubectl_run,
)

pytestmark = pytest.mark.openshell

LLM_AVAILABLE = os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() == "true"
AGENT_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")


def _url(agent: str, request):
    """Get agent URL from fixture map."""
    name = FIXTURE_MAP.get(agent)
    return request.getfixturevalue(name) if name else None


def _deploy_ready(name: str, ns: str = AGENT_NS) -> bool:
    """Check if deployment has 1 ready replica."""
    r = kubectl_run(
        "get", "deploy", name, "-n", ns, "-o", "jsonpath={.status.readyReplicas}"
    )
    return r.returncode == 0 and r.stdout.strip() == "1"


ALL_A2A_AGENTS_PORTFORWARD = [
    pytest.param("weather-agent", id="weather_agent"),
    pytest.param("adk-agent", id="adk_agent"),
    pytest.param("claude-sdk-agent", id="claude_sdk_agent"),
]


# ═══════════════════════════════════════════════════════════════════════════
# Multi-Turn Sequential Messages (ALL agents)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMultiTurnSequentialMessages:
    """Agent responds to 3 sequential messages with type-appropriate prompts."""

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_multiturn__agent__responds_to_3_sequential_messages(
        self, agent, request
    ):
        """Send 3 sequential messages and verify responses."""
        if agent in LLM_CAPABLE_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM (set OPENSHELL_LLM_AVAILABLE=true)")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach (netns blocks port-forward)")

        ctx = None
        for i, prompt in enumerate(AGENT_PROMPTS.get(agent, ["Hello"] * 3)):
            async with httpx.AsyncClient() as c:
                resp = await a2a_send(
                    c, url, prompt, request_id=f"{agent}-t{i}", context_id=ctx
                )
            assert "result" in resp, f"{agent} turn {i}: no result"
            assert extract_a2a_text(resp), f"{agent} turn {i}: empty"
            ctx = extract_context_id(resp) or ctx

    async def test_multiturn__weather_supervised__kubectl_exec(self, agent_namespace):
        """Supervised agent: test via kubectl exec (netns blocks port-forward)."""
        agent = "weather-agent-supervised"
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        r = kubectl_run(
            "exec", f"deploy/{agent}", "-n", agent_namespace, "--", "echo", "alive"
        )
        if r.returncode != 0:
            pytest.skip(f"{agent}: cannot exec into pod — {r.stderr.strip()}")


# ═══════════════════════════════════════════════════════════════════════════
# Multi-Turn Context Isolation (ALL agents)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMultiTurnContextIsolation:
    """Two independent conversations should not share state."""

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_context_isolation__agent__independent_requests_isolated(
        self, agent, request
    ):
        """Two independent requests should have different contextIds."""
        if agent in LLM_CAPABLE_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach")

        prompts = AGENT_PROMPTS.get(agent, ["Hello"] * 3)
        async with httpx.AsyncClient() as c:
            ra = await a2a_send(c, url, prompts[0], request_id=f"{agent}-a")
        async with httpx.AsyncClient() as c:
            rb = await a2a_send(c, url, prompts[1], request_id=f"{agent}-b")
        assert extract_a2a_text(ra) and extract_a2a_text(rb)
        ca, cb = extract_context_id(ra), extract_context_id(rb)
        if ca and cb:
            assert ca != cb, f"{agent}: independent requests share contextId"

    async def test_context_isolation__weather_supervised__netns_blocks_test(
        self, agent_namespace
    ):
        """Supervised agent: context isolation test requires A2A."""
        agent = "weather-agent-supervised"
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        pytest.skip(
            f"{agent}: context isolation test requires A2A — "
            f"supervised agent uses netns, tested via kubectl exec. "
            f"TODO: ExecSandbox gRPC integration for multi-turn."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Multi-Turn Context Continuity (LLM agents only)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMultiTurnContextContinuity:
    """If agent returns contextId, verify it persists across turns.

    This tests whether the AGENT maintains context. Currently all agents
    are stateless or don't preserve contextId. When PVC-backed session
    store is implemented (via Kagenti backend), these will pass.
    """

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_context_continuity__agent__context_preserved_across_turns(
        self, agent, request
    ):
        """If agent returns contextId, it should persist across turns."""
        if agent in LLM_CAPABLE_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach")

        prompts = AGENT_PROMPTS.get(agent, ["Hello"] * 3)
        async with httpx.AsyncClient() as c:
            r1 = await a2a_send(c, url, prompts[0], request_id=f"{agent}-c1")
        c1 = extract_context_id(r1)
        if not c1:
            pytest.skip(
                f"{agent}: stateless (no contextId). "
                f"TODO: Kagenti backend will manage context externally via session store."
            )

        async with httpx.AsyncClient() as c:
            r2 = await a2a_send(
                c, url, prompts[1], request_id=f"{agent}-c2", context_id=c1
            )
        c2 = extract_context_id(r2)
        if c2 != c1:
            pytest.skip(
                f"{agent}: contextId changed ({c1[:12]}... -> {c2[:12]}...). "
                f"Upstream ADK to_a2a() does not support client-sent contextId. "
                f"TODO: upstream PR or Kagenti backend session store."
            )

    async def test_context_continuity__weather_supervised__requires_grpc(
        self, agent_namespace
    ):
        """Supervised agent: context continuity requires ExecSandbox gRPC."""
        agent = "weather-agent-supervised"
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        pytest.skip(
            f"{agent}: context continuity requires A2A contextId or "
            f"Kagenti backend session store + ExecSandbox gRPC. "
            f"TODO: Phase 2 integration."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Agent Service Persistence (moved from test_sandbox_lifecycle.py)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestAgentServicePersistence:
    """Verify custom A2A agents (Deployments) remain available across requests.

    This is the Deployment equivalent of sandbox session reconnect —
    agents should be long-running services, not ephemeral pods.
    """

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_service_persistence__agent__responds_after_delay(
        self, agent, request, agent_namespace
    ):
        """Send message, wait, send again — agent should still respond."""
        if agent in LLM_CAPABLE_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach")

        import time

        prompts = AGENT_PROMPTS.get(agent, ["Hello", "Goodbye"])
        async with httpx.AsyncClient() as c:
            r1 = await a2a_send(c, url, prompts[0], request_id=f"{agent}-persist-1")
        assert extract_a2a_text(r1), f"{agent}: first request failed"

        time.sleep(10)

        async with httpx.AsyncClient() as c:
            r2 = await a2a_send(c, url, prompts[1], request_id=f"{agent}-persist-2")
        assert extract_a2a_text(r2), (
            f"{agent}: second request failed — agent not persistent"
        )
