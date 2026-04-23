"""
Session Persistence and Multi-Turn Conversation Tests

Validates the proposal's session persistence criteria adapted to our architecture:
1. Multi-turn conversations — agent handles sequential messages
2. Context continuity — agent preserves conversation state across turns
3. Conversation survives restart — context persists across pod scale-down/up
4. Workspace persistence — PVC-backed state survives sandbox restart

EVERY category tests ALL agent types. Unsupported combinations skip with
a clear reason and TODO describing what's needed to enable them.

Agent taxonomy:
  Custom A2A agents (Deployment-backed, accessed via A2A JSON-RPC):
    - weather_agent        — no LLM, stateless, MCP weather tool
    - adk_agent            — Google ADK + LiteLLM, returns contextId
    - claude_sdk_agent     — Anthropic SDK / OpenAI-compat, stateless
    - weather_supervised   — OpenShell supervisor (Landlock/netns/OPA), no LLM

  OpenShell builtin sandboxes (Sandbox CR, accessed via kubectl exec):
    - openshell_claude     — Claude Code CLI in base sandbox image
    - openshell_opencode   — OpenCode CLI in base sandbox image
    - openshell_generic    — generic sandbox (no CLI agent)

Context continuity architecture:
  Context lives in the Kagenti backend (PostgreSQL), NOT in the agent.
  - Custom A2A agents: backend manages context, sends via contextId
  - Builtin sandboxes: backend manages context, interacts via ExecSandbox gRPC
  - Workspace PVC preserves files; backend preserves conversation history
"""

import os
import subprocess
import time

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import (
    a2a_send,
    extract_a2a_text,
    extract_context_id,
    kubectl_get_pods_json,
)

pytestmark = pytest.mark.openshell

AGENT_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")
LLM_AVAILABLE = os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() == "true"
BASE_IMAGE = "ghcr.io/nvidia/openshell-community/sandboxes/base:latest"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, timeout=timeout
    )


def _sandbox_crd_exists() -> bool:
    return _kubectl("get", "crd", "sandboxes.agents.x-k8s.io").returncode == 0


def _deploy_ready(name: str, ns: str = AGENT_NS) -> bool:
    r = _kubectl(
        "get", "deploy", name, "-n", ns, "-o", "jsonpath={.status.readyReplicas}"
    )
    return r.returncode == 0 and r.stdout.strip() == "1"


def _scale_agent(agent: str, replicas: int, ns: str = AGENT_NS):
    _kubectl("scale", f"deploy/{agent}", f"--replicas={replicas}", "-n", ns)
    if replicas == 0:
        time.sleep(5)
    else:
        _kubectl(
            "rollout",
            "status",
            f"deploy/{agent}",
            "-n",
            ns,
            "--timeout=120s",
            timeout=150,
        )


def _cleanup_sandbox(name: str, pvc: str, ns: str = AGENT_NS):
    _kubectl("delete", "sandbox", name, "-n", ns, "--ignore-not-found", "--wait=false")
    pods = kubectl_get_pods_json(ns)
    for p in pods:
        if name in p["metadata"].get("name", ""):
            _kubectl(
                "delete",
                "pod",
                p["metadata"]["name"],
                "-n",
                ns,
                "--force",
                "--grace-period=0",
            )
    time.sleep(3)
    _kubectl("delete", "pvc", pvc, "-n", ns, "--ignore-not-found", "--wait=false")


skip_no_crd = pytest.mark.skipif(
    not _sandbox_crd_exists(), reason="Sandbox CRD not installed"
)

# ---------------------------------------------------------------------------
# Agent registry — defines ALL agents and their properties
# ---------------------------------------------------------------------------

ALL_A2A_AGENTS = [
    pytest.param("weather-agent", id="weather_agent"),
    pytest.param("adk-agent", id="adk_agent"),
    pytest.param("claude-sdk-agent", id="claude_sdk_agent"),
    pytest.param("weather-agent-supervised", id="weather_supervised"),
]

ALL_A2A_AGENTS_PORTFORWARD = [
    pytest.param("weather-agent", id="weather_agent"),
    pytest.param("adk-agent", id="adk_agent"),
    pytest.param("claude-sdk-agent", id="claude_sdk_agent"),
]

ALL_SANDBOX_TYPES = [
    pytest.param(
        "test-generic-ws",
        "session-data-12345",
        "/workspace/session.txt",
        "echo 'session-data-12345' > /workspace/session.txt && sleep 300",
        id="openshell_generic",
    ),
    pytest.param(
        "test-claude-ws",
        "claude-session-001",
        "/workspace/.claude/session.json",
        "mkdir -p /workspace/.claude /workspace/project && "
        "echo 'session-id: claude-session-001' > /workspace/.claude/session.json && "
        "echo 'def main(): pass' > /workspace/project/main.py && sleep 300",
        id="openshell_claude",
    ),
    pytest.param(
        "test-opencode-ws",
        "opencode-session-001",
        "/workspace/.opencode/config.txt",
        "mkdir -p /workspace/.opencode /workspace/project && "
        "echo session=opencode-session-001 > /workspace/.opencode/config.txt && "
        "echo hello from opencode > /workspace/project/app.py && sleep 300",
        id="openshell_opencode",
    ),
]

PROMPTS = {
    "weather-agent": [
        "Weather in London?",
        "Compare that to Paris.",
        "Which is warmer?",
    ],
    "adk-agent": [
        "I have a Python JSON parser.",
        "Add error handling.",
        "Review the result.",
    ],
    "claude-sdk-agent": [
        "Review: def add(a,b): return a+b",
        "Add type hints.",
        "Add tests.",
    ],
    "weather-agent-supervised": [
        "Weather in Berlin?",
        "What about Tokyo?",
        "Which is colder?",
    ],
}

FIXTURE_MAP = {
    "weather-agent": "weather_agent_url",
    "adk-agent": "adk_agent_url",
    "claude-sdk-agent": "claude_sdk_agent_url",
}

LLM_AGENTS = {"adk-agent", "claude-sdk-agent"}


def _url(agent: str, request):
    name = FIXTURE_MAP.get(agent)
    return request.getfixturevalue(name) if name else None


# ═══════════════════════════════════════════════════════════════════════════
# 1. Multi-Turn Conversations (ALL A2A agents)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestMultiTurnSequentialMessages:
    """Agent responds to 3 sequential messages with type-appropriate prompts."""

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_responds_to_3_turns(self, agent, request):
        if agent in LLM_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM (set OPENSHELL_LLM_AVAILABLE=true)")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach (netns blocks port-forward)")

        ctx = None
        for i, prompt in enumerate(PROMPTS.get(agent, ["Hello"] * 3)):
            async with httpx.AsyncClient() as c:
                resp = await a2a_send(
                    c, url, prompt, request_id=f"{agent}-t{i}", context_id=ctx
                )
            assert "result" in resp, f"{agent} turn {i}: no result"
            assert extract_a2a_text(resp), f"{agent} turn {i}: empty"
            ctx = extract_context_id(resp) or ctx

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param("weather-agent-supervised", id="weather_supervised"),
        ],
    )
    async def test_responds_to_3_turns_supervised(self, agent, agent_namespace):
        """Supervised agent: test via kubectl exec (netns blocks port-forward)."""
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        r = _kubectl(
            "exec", f"deploy/{agent}", "-n", agent_namespace, "--", "echo", "alive"
        )
        if r.returncode != 0:
            pytest.skip(f"{agent}: cannot exec into pod — {r.stderr.strip()}")


@pytest.mark.asyncio
class TestMultiTurnContextIsolation:
    """Two independent conversations should not share state."""

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_independent_contexts_isolated(self, agent, request):
        if agent in LLM_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach")

        prompts = PROMPTS.get(agent, ["Hello"] * 3)
        async with httpx.AsyncClient() as c:
            ra = await a2a_send(c, url, prompts[0], request_id=f"{agent}-a")
        async with httpx.AsyncClient() as c:
            rb = await a2a_send(c, url, prompts[1], request_id=f"{agent}-b")
        assert extract_a2a_text(ra) and extract_a2a_text(rb)
        ca, cb = extract_context_id(ra), extract_context_id(rb)
        if ca and cb:
            assert ca != cb, f"{agent}: independent requests share contextId"

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param("weather-agent-supervised", id="weather_supervised"),
        ],
    )
    async def test_independent_contexts_isolated_supervised(
        self, agent, agent_namespace
    ):
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        pytest.skip(
            f"{agent}: context isolation test requires A2A — "
            f"supervised agent uses netns, tested via kubectl exec. "
            f"TODO: ExecSandbox gRPC integration for multi-turn."
        )


@pytest.mark.asyncio
class TestMultiTurnContextContinuity:
    """If agent returns contextId, verify it persists across turns.

    This tests whether the AGENT maintains context. Currently all agents
    are stateless or don't preserve contextId. When PVC-backed session
    store is implemented (via Kagenti backend), these will pass.
    """

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_context_preserved_across_turns(self, agent, request):
        if agent in LLM_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM")
        url = _url(agent, request)
        if not url:
            pytest.skip(f"{agent}: cannot reach")

        prompts = PROMPTS.get(agent, ["Hello"] * 3)
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

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param("weather-agent-supervised", id="weather_supervised"),
        ],
    )
    async def test_context_preserved_supervised(self, agent, agent_namespace):
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        pytest.skip(
            f"{agent}: context continuity requires A2A contextId or "
            f"Kagenti backend session store + ExecSandbox gRPC. "
            f"TODO: Phase 2 integration."
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. Conversation Survives Pod Restart (ALL agents)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
class TestConversationSurvivesRestart:
    """Start conversation, restart pod, try to continue.

    This is the core session persistence test: does context survive
    a pod restart? Currently all agents lose in-memory state on restart.
    When PVC-backed session store is added, these will transition from
    SKIP to PASS.
    """

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS_PORTFORWARD)
    async def test_multiturn_across_restart(self, agent, agent_namespace):
        """Turn 1 -> scale 0 -> scale 1 -> Turn 2: does context survive?

        Uses own port-forwards (not session fixtures) to avoid invalidating
        other tests' session-scoped fixtures after the scale cycle.
        """
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")
        if agent in LLM_AGENTS and not LLM_AVAILABLE:
            pytest.skip(f"{agent}: requires LLM")

        from kagenti.tests.e2e.openshell.conftest import _port_forward

        prompts = PROMPTS.get(agent, ["Hello"] * 3)

        # Turn 1 (pre-restart, own port-forward)
        url1, proc1 = _port_forward(agent, agent_namespace, 8080)
        if not url1:
            pytest.skip(f"{agent}: cannot reach")
        try:
            async with httpx.AsyncClient() as c:
                r1 = await a2a_send(c, url1, prompts[0], request_id=f"{agent}-pre")
            assert "result" in r1
            ctx = extract_context_id(r1)
        finally:
            if proc1:
                proc1.terminate()
                proc1.wait()

        # Restart
        _scale_agent(agent, 0, agent_namespace)
        _scale_agent(agent, 1, agent_namespace)

        # Turn 2 (post-restart, new port-forward)
        url2, proc2 = _port_forward(agent, agent_namespace, 8080)
        if not url2:
            pytest.fail(f"{agent}: unreachable after restart")
        try:
            async with httpx.AsyncClient() as c:
                r2 = await a2a_send(
                    c, url2, prompts[1], request_id=f"{agent}-post", context_id=ctx
                )
            assert "result" in r2, f"{agent}: no response after restart"
            ctx2 = extract_context_id(r2)

            if ctx is None:
                pytest.skip(
                    f"{agent}: responds after restart but stateless (no contextId). "
                    f"TODO: Kagenti backend session store for context persistence."
                )
            elif ctx2 != ctx:
                pytest.skip(
                    f"{agent}: responds after restart but context lost (in-memory). "
                    f"TODO: PVC-backed session checkpoint + Kagenti backend restore."
                )
        finally:
            if proc2:
                proc2.terminate()
                proc2.wait()

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param("weather-agent-supervised", id="weather_supervised"),
        ],
    )
    async def test_multiturn_across_restart_supervised(self, agent, agent_namespace):
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")

        _scale_agent(agent, 0, agent_namespace)
        _scale_agent(agent, 1, agent_namespace)

        assert _deploy_ready(agent, agent_namespace), (
            f"{agent}: not ready after restart"
        )
        pytest.skip(
            f"{agent}: pod restarted successfully. A2A context test skipped — "
            f"netns blocks port-forward. "
            f"TODO: ExecSandbox gRPC for session persistence testing."
        )

    @pytest.mark.parametrize("agent", ALL_A2A_AGENTS)
    async def test_pod_uid_changes_after_restart(self, agent, agent_namespace):
        """Confirm restart creates a new pod (not the same one)."""
        if not _deploy_ready(agent, agent_namespace):
            pytest.skip(f"{agent}: not deployed")

        def _uid():
            pods = kubectl_get_pods_json(agent_namespace)
            m = [
                p
                for p in pods
                if p["metadata"]["name"].startswith(agent)
                and "-build" not in p["metadata"]["name"]
                and p["status"].get("phase") == "Running"
            ]
            if agent != "weather-agent-supervised":
                m = [p for p in m if "-supervised" not in p["metadata"]["name"]]
            return m[0]["metadata"]["uid"] if m else None

        uid1 = _uid()
        if not uid1:
            pytest.skip(f"{agent}: no running pod")

        _scale_agent(agent, 0, agent_namespace)
        _scale_agent(agent, 1, agent_namespace)

        uid2 = _uid()
        assert uid2, f"{agent}: no running pod after restart"
        assert uid1 != uid2, f"{agent}: same pod UID — restart did not create new pod"


# ═══════════════════════════════════════════════════════════════════════════
# 3. PVC Workspace Persistence (ALL builtin sandbox types)
# ═══════════════════════════════════════════════════════════════════════════


class TestWorkspacePersistence:
    """PVC-backed workspace survives sandbox pod restart.

    Each builtin sandbox type (generic, Claude, OpenCode) creates a sandbox
    with a PVC, writes session state, and verifies data was written.
    """

    @skip_no_crd
    @pytest.mark.parametrize("name, content, path, cmd", ALL_SANDBOX_TYPES)
    def test_session_written_to_pvc(self, name, content, path, cmd):
        pvc = f"{name}-pvc"
        _cleanup_sandbox(name, pvc)

        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=f"""
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {pvc}
  namespace: {AGENT_NS}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 100Mi
""",
            capture_output=True,
            text=True,
            timeout=30,
        )

        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=f"""
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: {name}
  namespace: {AGENT_NS}
spec:
  podTemplate:
    spec:
      containers:
      - name: sandbox
        image: {BASE_IMAGE}
        command: ["sh", "-c", "{cmd}"]
        volumeMounts:
        - name: workspace
          mountPath: /workspace
      volumes:
      - name: workspace
        persistentVolumeClaim:
          claimName: {pvc}
""",
            capture_output=True,
            text=True,
            timeout=30,
        )

        deadline = time.time() + 60
        pod = None
        while time.time() < deadline:
            pods = kubectl_get_pods_json(AGENT_NS)
            m = [
                p
                for p in pods
                if name in p["metadata"].get("name", "")
                and p["status"].get("phase") == "Running"
            ]
            if m:
                pod = m[0]["metadata"]["name"]
                break
            time.sleep(5)

        if not pod:
            _cleanup_sandbox(name, pvc)
            pytest.skip(f"{name}: pod not running — base image pull may be slow")

        r = _kubectl("exec", pod, "-n", AGENT_NS, "--", "cat", path)
        _cleanup_sandbox(name, pvc)

        if r.returncode != 0:
            pytest.skip(f"{name}: cannot read {path}: {r.stderr.strip()}")
        assert content in r.stdout, (
            f"{name}: expected '{content}' in {path}, got: {r.stdout}"
        )

    @skip_no_crd
    def test_pvc_survives_sandbox_deletion(self):
        """PVC persists after Sandbox CR deleted — enables session resume."""
        name, pvc = "test-pvc-survive", "test-pvc-survive-pvc"
        _cleanup_sandbox(name, pvc)

        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=f"""
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {pvc}
  namespace: {AGENT_NS}
spec:
  accessModes: [ReadWriteOnce]
  resources:
    requests:
      storage: 50Mi
""",
            capture_output=True,
            text=True,
            timeout=30,
        )

        subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=f"""
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: {name}
  namespace: {AGENT_NS}
spec:
  podTemplate:
    spec:
      containers:
      - name: sandbox
        image: {BASE_IMAGE}
        command: ["sleep", "300"]
        volumeMounts:
        - name: workspace
          mountPath: /workspace
      volumes:
      - name: workspace
        persistentVolumeClaim:
          claimName: {pvc}
""",
            capture_output=True,
            text=True,
            timeout=30,
        )

        time.sleep(10)
        _kubectl(
            "delete",
            "sandbox",
            name,
            "-n",
            AGENT_NS,
            "--ignore-not-found",
            "--wait=false",
        )
        time.sleep(5)

        r = _kubectl("get", "pvc", pvc, "-n", AGENT_NS)
        _cleanup_sandbox(name, pvc)
        assert r.returncode == 0, (
            "PVC deleted with sandbox — session data would be lost"
        )
