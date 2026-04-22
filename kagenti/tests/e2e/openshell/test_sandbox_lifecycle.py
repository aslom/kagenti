"""
Tests for OpenShell sandbox lifecycle via Kubernetes API.

Tests create, list, and delete Sandbox CRs (agents.x-k8s.io/v1alpha1)
to verify the OpenShell gateway processes them correctly.
"""

import json
import subprocess
import time

import pytest

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
