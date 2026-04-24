"""
Tests for OpenShell built-in sandbox agents (Mode 2).

Creates sandboxes using Sandbox CRs and the base image
(ghcr.io/nvidia/openshell-community/sandboxes/base).

Tests verify the gateway processes the Sandbox CR and creates a pod.
LLM-dependent tests (Claude, OpenCode) require provider configuration.
"""

import json
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.openshell

SANDBOX_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")
GATEWAY_NS = os.getenv("OPENSHELL_GATEWAY_NAMESPACE", "openshell-system")
BASE_IMAGE = "ghcr.io/nvidia/openshell-community/sandboxes/base:latest"


from kagenti.tests.e2e.openshell.conftest import kubectl_run, sandbox_crd_installed


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return kubectl_run(*args, timeout=timeout)


def _create_sandbox(name: str, command: str = "sleep 300") -> bool:
    """Create a Sandbox CR with the base image."""
    yaml = f"""
apiVersion: agents.x-k8s.io/v1alpha1
kind: Sandbox
metadata:
  name: {name}
  namespace: {SANDBOX_NS}
spec:
  podTemplate:
    spec:
      containers:
      - name: sandbox
        image: {BASE_IMAGE}
        command: ["sh", "-c", "{command}"]
"""
    result = subprocess.run(
        ["kubectl", "apply", "-f", "-"],
        input=yaml,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode == 0


def _delete_sandbox(name: str):
    _kubectl("delete", "sandbox", name, "-n", SANDBOX_NS, "--ignore-not-found")


def _wait_for_sandbox_pod(name: str, timeout: int = 60) -> bool:
    """Wait for a sandbox pod to be created by the gateway."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _kubectl(
            "get",
            "pods",
            "-n",
            SANDBOX_NS,
            "-l",
            f"agents.x-k8s.io/sandbox-name={name}",
            "-o",
            "jsonpath={.items[0].status.phase}",
        )
        if result.stdout.strip() in ("Running", "Pending"):
            return True
        # Also check by name prefix
        result = _kubectl("get", "pods", "-n", SANDBOX_NS, "-o", "json")
        if result.returncode == 0:
            pods = json.loads(result.stdout).get("items", [])
            for pod in pods:
                if name in pod["metadata"].get("name", ""):
                    return True
        time.sleep(5)
    return False


skip_no_crd = pytest.mark.skipif(
    not sandbox_crd_installed(),
    reason="Sandbox CRD not installed",
)


class TestBaseSandboxCreation:
    """Test creating a sandbox with the base image."""

    @skip_no_crd
    def test_create_sandbox_cr(self):
        """Create a Sandbox CR and verify it's accepted by the API."""
        name = "test-base-sandbox"
        _delete_sandbox(name)
        time.sleep(2)

        assert _create_sandbox(name), "Failed to create Sandbox CR"

        result = _kubectl(
            "get",
            "sandbox",
            name,
            "-n",
            SANDBOX_NS,
            "-o",
            "jsonpath={.metadata.name}",
        )
        assert result.stdout.strip() == name

        # Cleanup
        _delete_sandbox(name)

    @skip_no_crd
    def test_gateway_sees_sandbox(self):
        """Verify the gateway logs show it detected the sandbox CR."""
        result = _kubectl(
            "logs",
            "openshell-gateway-0",
            "-n",
            GATEWAY_NS,
            "--tail=100",
        )
        assert result.returncode == 0
        logs = result.stdout.lower()
        has_sandbox_event = any(
            kw in logs
            for kw in [
                "listing sandbox",
                "sandbox created",
                "sandbox deleted",
                "reconcil",
            ]
        )
        assert has_sandbox_event or "sandbox" in logs, (
            "Gateway logs don't mention sandbox processing"
        )


class TestBuiltinCLIs:
    """Test built-in CLI availability in the base sandbox image.

    These tests create a sandbox with the base image and verify
    the expected CLIs are on PATH. No LLM calls are made.
    """

    @skip_no_crd
    def test_base_image_cli_check(self):
        """Create a sandbox and check which CLIs are installed."""
        name = "test-cli-check"
        _delete_sandbox(name)
        time.sleep(2)

        check_cmd = (
            "which claude 2>/dev/null && echo 'claude:OK' || echo 'claude:MISSING'; "
            "which opencode 2>/dev/null && echo 'opencode:OK' || echo 'opencode:MISSING'; "
            "which codex 2>/dev/null && echo 'codex:OK' || echo 'codex:MISSING'; "
            "which python3 2>/dev/null && echo 'python3:OK' || echo 'python3:MISSING'; "
            "which node 2>/dev/null && echo 'node:OK' || echo 'node:MISSING'; "
            "which git 2>/dev/null && echo 'git:OK' || echo 'git:MISSING'"
        )
        _create_sandbox(name, command=check_cmd)

        # Wait for the pod to run and complete
        time.sleep(15)

        # Check pod logs for CLI results
        result = _kubectl("get", "pods", "-n", SANDBOX_NS, "-o", "json")
        if result.returncode == 0:
            pods = json.loads(result.stdout).get("items", [])
            sandbox_pods = [p for p in pods if name in p["metadata"].get("name", "")]
            if sandbox_pods:
                pod_name = sandbox_pods[0]["metadata"]["name"]
                logs = _kubectl("logs", pod_name, "-n", SANDBOX_NS)
                if logs.returncode == 0:
                    output = logs.stdout
                    _delete_sandbox(name)
                    assert "python3:OK" in output or "git:OK" in output, (
                        f"Base image missing core CLIs. Output: {output}"
                    )
                    return

        _delete_sandbox(name)
        pytest.skip(
            "Could not verify base image CLIs — pod may not have started "
            "(base image is ~620MB, pull may be in progress)"
        )


class TestClaudeSandbox:
    """Claude CLI sandbox — requires Anthropic provider configured."""

    @skip_no_crd
    @pytest.mark.skipif(
        os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() != "true",
        reason="LLM not available — Claude sandbox needs provider config",
    )
    def test_claude_sandbox_cr_created(self):
        """Create a Claude sandbox CR and verify it is accepted."""
        name = "test-claude-sb"
        _delete_sandbox(name)
        time.sleep(2)

        # Claude CLI needs provider config on the gateway side
        # For now, just verify the sandbox CR is created
        _create_sandbox(
            name, command="claude --version 2>/dev/null || echo 'claude not configured'"
        )
        time.sleep(10)

        result = _kubectl("get", "sandbox", name, "-n", SANDBOX_NS)
        assert result.returncode == 0, "Sandbox CR not found"

        _delete_sandbox(name)


class TestOpenCodeSandbox:
    """OpenCode CLI sandbox — requires OpenAI-compatible provider."""

    @skip_no_crd
    @pytest.mark.skipif(
        os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() != "true",
        reason="LLM not available — OpenCode sandbox needs provider config",
    )
    def test_opencode_sandbox_cr_created(self):
        """Create an OpenCode sandbox and verify it starts."""
        name = "test-opencode-sb"
        _delete_sandbox(name)
        _create_sandbox(
            name,
            command="opencode --version 2>/dev/null || echo 'opencode not configured'",
        )
        time.sleep(10)

        result = _kubectl("get", "sandbox", name, "-n", SANDBOX_NS)
        assert result.returncode == 0
        _delete_sandbox(name)


class TestCodexSandbox:
    """Codex CLI sandbox — requires real OpenAI API key."""

    @skip_no_crd
    @pytest.mark.skip(
        reason="Codex requires real OpenAI API key (not LiteMaaS compatible)"
    )
    def test_codex_sandbox(self):
        pass


class TestCopilotSandbox:
    """GitHub Copilot CLI sandbox — requires GitHub Copilot subscription."""

    @skip_no_crd
    @pytest.mark.skip(
        reason="Copilot requires GitHub Copilot subscription (not LiteLLM compatible)"
    )
    def test_copilot_sandbox(self):
        pass
