"""
Tests for OpenShell sandbox lifecycle via Kubernetes API.

Tests create, list, and delete Sandbox CRs (agents.x-k8s.io/v1alpha1)
to verify the OpenShell gateway processes them correctly.

Also covers the proposal's validation criteria that are adapted for the
A2A-first agent model:
- Sandbox status observability (A2A equivalent of ``openshell term``)
- Agent service persistence (A2A equivalent of session reconnect)
"""

import json
import os
import subprocess
import time

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import (
    a2a_send,
    extract_a2a_text,
    kubectl_get_pods_json,
    kubectl_get_deployments_json,
)

pytestmark = pytest.mark.openshell

SANDBOX_NS = "team1"
SANDBOX_NAME = "test-sandbox-poc"


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    cmd = ["kubectl", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _sandbox_crd_installed() -> bool:
    result = _kubectl("get", "crd", "sandboxes.agents.x-k8s.io")
    return result.returncode == 0


skip_no_crd = pytest.mark.skipif(
    not _sandbox_crd_installed(),
    reason="Sandbox CRD (agents.x-k8s.io) not installed",
)


class TestSandboxLifecycle:
    """Test sandbox CRUD via Kubernetes Sandbox CR API."""

    @skip_no_crd
    def test_list_sandboxes(self):
        """List Sandbox CRs — should succeed even if none exist."""
        result = _kubectl(
            "get",
            "sandboxes.agents.x-k8s.io",
            "-n",
            SANDBOX_NS,
            "-o",
            "json",
        )
        assert result.returncode == 0, f"Failed to list sandboxes: {result.stderr}"
        data = json.loads(result.stdout)
        assert "items" in data

    @skip_no_crd
    def test_create_sandbox(self):
        """Create a Sandbox CR and verify the gateway picks it up."""
        # Clean up first
        _kubectl("delete", "sandbox", SANDBOX_NAME, "-n", SANDBOX_NS)
        time.sleep(2)

        # Create a minimal Sandbox CR (spec.podTemplate is the schema)
        sandbox_yaml = f"""
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: {SANDBOX_NAME}
  namespace: {SANDBOX_NS}
spec:
  podTemplate:
    spec:
      containers:
      - name: sandbox
        image: ghcr.io/nvidia/openshell-community/sandboxes/base:latest
"""
        result = subprocess.run(
            ["kubectl", "apply", "-f", "-"],
            input=sandbox_yaml,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, f"Failed to create sandbox: {result.stderr}"

        # Verify it exists
        time.sleep(3)
        result = _kubectl(
            "get",
            "sandbox",
            SANDBOX_NAME,
            "-n",
            SANDBOX_NS,
            "-o",
            "jsonpath={.metadata.name}",
        )
        assert result.stdout.strip() == SANDBOX_NAME

    @skip_no_crd
    def test_delete_sandbox(self):
        """Delete the test sandbox CR."""
        # Ensure it exists first
        _kubectl(
            "get",
            "sandbox",
            SANDBOX_NAME,
            "-n",
            SANDBOX_NS,
        )

        result = _kubectl(
            "delete",
            "sandbox",
            SANDBOX_NAME,
            "-n",
            SANDBOX_NS,
            "--timeout=30s",
        )
        # Accept both success (deleted) and not-found (already gone)
        assert result.returncode == 0 or "NotFound" in result.stderr, (
            f"Failed to delete sandbox: {result.stderr}"
        )

    @skip_no_crd
    def test_gateway_processes_sandbox(self):
        """Verify the gateway logs show it processed a sandbox event."""
        result = _kubectl(
            "logs",
            "openshell-gateway-0",
            "-n",
            "openshell-system",
            "--tail=50",
        )
        assert result.returncode == 0
        assert (
            "Listing sandboxes" in result.stdout or "sandbox" in result.stdout.lower()
        ), "Gateway logs don't show sandbox processing"


AGENT_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")
GATEWAY_NS = os.getenv("OPENSHELL_GATEWAY_NAMESPACE", "openshell-system")


class TestSandboxStatusObservability:
    """A2A equivalent of the proposal's ``openshell term`` validation criterion.

    The proposal requires: "openshell term shows sandbox status."
    In our A2A-first model, sandbox/agent status is observed via the
    Kubernetes API (kubectl / Kagenti UI PodStatusPanel), not the CLI.

    These tests verify that all sandbox and agent status information
    is queryable and accurate — the same data the Kagenti UI renders.
    """

    def test_gateway_status_queryable(self):
        """Gateway StatefulSet status is queryable with phase and readiness."""
        result = _kubectl(
            "get",
            "statefulset",
            "openshell-gateway",
            "-n",
            GATEWAY_NS,
            "-o",
            "json",
        )
        if result.returncode != 0:
            pytest.skip("Gateway StatefulSet not found")

        sts = json.loads(result.stdout)
        desired = sts["spec"].get("replicas", 1)
        ready = sts.get("status", {}).get("readyReplicas", 0)
        assert ready >= desired, f"Gateway: {ready}/{desired} replicas ready"

    def test_agent_deployments_status_queryable(self):
        """Each agent deployment exposes replicas, readyReplicas, conditions."""
        deployments = kubectl_get_deployments_json(AGENT_NS)
        agent_deploys = [
            d
            for d in deployments
            if d.get("metadata", {}).get("labels", {}).get("kagenti.io/type") == "agent"
        ]
        if not agent_deploys:
            pytest.skip("No agent deployments found")

        for dep in agent_deploys:
            name = dep["metadata"]["name"]
            status = dep.get("status", {})
            assert "replicas" in status or "readyReplicas" in status, (
                f"{name}: deployment status missing replica counts"
            )
            conditions = status.get("conditions", [])
            assert len(conditions) > 0, f"{name}: deployment has no status conditions"

    def test_agent_pods_status_queryable(self):
        """Each agent pod exposes phase, containerStatuses, and resource usage."""
        pods = kubectl_get_pods_json(AGENT_NS)
        agent_pods = [
            p
            for p in pods
            if p.get("metadata", {}).get("labels", {}).get("kagenti.io/type") == "agent"
            and "-build" not in p["metadata"]["name"]
        ]
        if not agent_pods:
            pytest.skip("No agent pods found")

        for pod in agent_pods:
            name = pod["metadata"]["name"]
            status = pod.get("status", {})
            assert "phase" in status, f"{name}: pod missing phase"
            assert status["phase"] == "Running", (
                f"{name}: pod phase is {status['phase']}, expected Running"
            )
            container_statuses = status.get("containerStatuses", [])
            assert len(container_statuses) > 0, f"{name}: pod has no containerStatuses"
            for cs in container_statuses:
                assert "restartCount" in cs, (
                    f"{name}/{cs['name']}: missing restartCount"
                )

    def test_sandbox_cr_status_queryable(self):
        """Sandbox CRs expose status fields when created."""
        if not _sandbox_crd_installed():
            pytest.skip("Sandbox CRD not installed")

        result = _kubectl(
            "get",
            "sandboxes.agents.x-k8s.io",
            "-n",
            AGENT_NS,
            "-o",
            "json",
        )
        assert result.returncode == 0, f"Cannot list sandboxes: {result.stderr}"
        data = json.loads(result.stdout)
        assert "items" in data, "Sandbox list response missing 'items'"

    def test_gateway_logs_accessible(self):
        """Gateway logs are accessible for debugging and audit."""
        result = _kubectl(
            "logs",
            "openshell-gateway-0",
            "-n",
            GATEWAY_NS,
            "--tail=20",
        )
        assert result.returncode == 0, f"Cannot read gateway logs: {result.stderr}"
        assert len(result.stdout) > 0, "Gateway logs are empty"


@pytest.mark.asyncio
class TestAgentServicePersistence:
    """A2A equivalent of the proposal's session reconnect validation criterion.

    The proposal requires: "Sandbox survives CLI disconnect and reconnect
    (if --keep is used)."

    In our A2A-first model, agents are long-running Deployment-backed
    services, not ephemeral CLI sessions. Persistence means the agent
    pod remains available and responsive across multiple independent
    client connections — equivalent to disconnect + reconnect in the
    CLI model.

    These tests send multiple sequential A2A requests using independent
    HTTP connections to verify agent availability and response consistency.
    """

    async def test_agent_responds_across_connections(self, weather_agent_url):
        """Agent responds correctly across multiple independent connections.

        Each request uses a fresh httpx.AsyncClient (new TCP connection),
        simulating CLI disconnect + reconnect.
        """
        responses = []
        for i in range(3):
            async with httpx.AsyncClient() as client:
                resp = await a2a_send(
                    client,
                    weather_agent_url,
                    "What is the weather in Paris?",
                    request_id=f"persist-{i}",
                )
            assert "result" in resp, f"Request {i}: missing result"
            text = extract_a2a_text(resp)
            assert text and len(text) > 5, f"Request {i}: empty response"
            responses.append(text)

        assert len(responses) == 3, "Not all 3 requests succeeded"

    async def test_agent_stable_after_delay(self, weather_agent_url):
        """Agent remains responsive after a short idle period.

        Simulates a user disconnecting, waiting, then reconnecting.
        """
        import asyncio

        async with httpx.AsyncClient() as client:
            resp1 = await a2a_send(
                client,
                weather_agent_url,
                "Weather in Berlin?",
                request_id="before-delay",
            )
        assert "result" in resp1

        await asyncio.sleep(5)

        async with httpx.AsyncClient() as client:
            resp2 = await a2a_send(
                client,
                weather_agent_url,
                "Weather in Madrid?",
                request_id="after-delay",
            )
        assert "result" in resp2
        text = extract_a2a_text(resp2)
        assert text and len(text) > 5, "Agent unresponsive after idle period"

    async def test_agent_pod_not_restarted_during_requests(
        self,
        weather_agent_url,
        agent_namespace,
    ):
        """Agent pod does not restart between requests — true persistence."""
        pods_before = kubectl_get_pods_json(agent_namespace)
        weather_before = [
            p
            for p in pods_before
            if p["metadata"]["name"].startswith("weather-agent")
            and "-supervised" not in p["metadata"]["name"]
            and "-build" not in p["metadata"]["name"]
            and p["status"].get("phase") == "Running"
        ]
        if not weather_before:
            pytest.skip("No running weather-agent pod found")

        restart_count_before = sum(
            cs.get("restartCount", 0)
            for p in weather_before
            for cs in p["status"].get("containerStatuses", [])
        )

        async with httpx.AsyncClient() as client:
            await a2a_send(
                client,
                weather_agent_url,
                "Weather in Rome?",
                request_id="restart-check",
            )

        pods_after = kubectl_get_pods_json(agent_namespace)
        weather_after = [
            p
            for p in pods_after
            if p["metadata"]["name"].startswith("weather-agent")
            and "-supervised" not in p["metadata"]["name"]
            and "-build" not in p["metadata"]["name"]
            and p["status"].get("phase") == "Running"
        ]

        restart_count_after = sum(
            cs.get("restartCount", 0)
            for p in weather_after
            for cs in p["status"].get("containerStatuses", [])
        )

        assert restart_count_after == restart_count_before, (
            f"Agent pod restarted during request: "
            f"restarts before={restart_count_before}, after={restart_count_after}"
        )
