"""
Credential Isolation E2E Tests (OpenShell PoC)

Validates that agent pods do not hold raw secrets and that the OpenShell
supervisor is the container entrypoint (when supervisor integration is
available).

These tests are currently **skipped** because the supervisor binary is
not yet integrated into agent images.  They serve as the test skeleton
for when supervisor integration lands.

Usage:
    pytest kagenti/tests/e2e/openshell/test_credential_isolation.py -v -m openshell
"""

import json
import subprocess

import pytest

from kagenti.tests.e2e.openshell.conftest import kubectl_get_pods_json


pytestmark = pytest.mark.openshell

# Agent deployments to inspect
_AGENTS = ["weather-agent", "adk-agent", "claude-sdk-agent"]


def _get_pod_name(agent: str, namespace: str) -> str:
    """Return the name of the first Running pod for a given agent prefix."""
    pods = kubectl_get_pods_json(namespace)
    for pod in pods:
        if (
            pod["metadata"]["name"].startswith(agent)
            and pod["status"].get("phase") == "Running"
        ):
            return pod["metadata"]["name"]
    pytest.skip(f"No running pod found for {agent} in {namespace}")


class TestCredentialPlaceholders:
    """Verify that env vars in agent pods use openshell:resolve:env: placeholders."""

    @pytest.mark.skip(
        reason="supervisor integration pending -- env placeholders not yet injected"
    )
    @pytest.mark.parametrize("agent", _AGENTS)
    def test_env_has_resolve_placeholders(self, agent, agent_namespace):
        """Agent pod env vars should contain openshell:resolve:env: placeholders
        instead of raw secret values.

        This ensures secrets are resolved at runtime by the supervisor,
        not baked into the pod spec.
        """
        pod_name = _get_pod_name(agent, agent_namespace)

        result = subprocess.run(
            [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                agent_namespace,
                "--",
                "env",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            pytest.fail(f"kubectl exec env failed: {result.stderr}")

        env_lines = result.stdout.strip().splitlines()

        # Check that sensitive env vars use the placeholder pattern
        sensitive_prefixes = ("OPENAI_API_KEY=", "ANTHROPIC_API_KEY=")
        for line in env_lines:
            for prefix in sensitive_prefixes:
                if line.startswith(prefix):
                    value = line[len(prefix) :]
                    assert value.startswith("openshell:resolve:env:"), (
                        f"Pod {pod_name}: {prefix.rstrip('=')} contains a raw value "
                        f"instead of an openshell:resolve:env: placeholder"
                    )


class TestSupervisorEntrypoint:
    """Verify that the OpenShell supervisor is the container entrypoint."""

    @pytest.mark.skip(
        reason="supervisor integration pending -- supervisor not yet injected"
    )
    @pytest.mark.parametrize("agent", _AGENTS)
    def test_supervisor_is_entrypoint(self, agent, agent_namespace):
        """The first process (PID 1) inside the agent container should be
        the OpenShell supervisor binary.
        """
        pod_name = _get_pod_name(agent, agent_namespace)

        result = subprocess.run(
            [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                agent_namespace,
                "--",
                "cat",
                "/proc/1/cmdline",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            pytest.skip(f"Cannot read /proc/1/cmdline in {pod_name}: {result.stderr}")

        # /proc/1/cmdline uses null bytes as delimiters
        cmdline = result.stdout.replace("\x00", " ").strip()
        assert "openshell-supervisor" in cmdline or "supervisor" in cmdline, (
            f"Pod {pod_name}: PID 1 is not the supervisor. cmdline: {cmdline}"
        )


class TestPolicyConfigMapMounted:
    """Verify that the OPA sandbox policy ConfigMap is mounted."""

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_policy_file_exists(self, agent, agent_namespace):
        """The policy ConfigMap should be mounted at /etc/openshell/policy.yaml."""
        pod_name = _get_pod_name(agent, agent_namespace)

        result = subprocess.run(
            [
                "kubectl",
                "exec",
                pod_name,
                "-n",
                agent_namespace,
                "--",
                "cat",
                "/etc/openshell/policy.yaml",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )

        if result.returncode != 0:
            pytest.fail(f"Policy file not found in {pod_name}: {result.stderr}")

        content = result.stdout.strip()
        assert "version" in content, (
            f"Policy file in {pod_name} does not look valid: {content[:200]}"
        )
        assert "filesystem_policy" in content, (
            f"Policy file in {pod_name} missing filesystem_policy section"
        )
