"""
Credential Isolation E2E Tests (OpenShell PoC)

Validates that agent pods handle credentials securely:
1. Secrets are delivered via K8s secretKeyRef (not hardcoded in deployment YAML)
2. Agent containers don't have unnecessary secrets in env
3. Policy ConfigMaps are properly mounted
4. When supervisor is integrated: placeholder tokens instead of real secrets
"""

import json
import subprocess

import pytest

from kagenti.tests.e2e.openshell.conftest import kubectl_get_pods_json


pytestmark = pytest.mark.openshell

_AGENTS = ["weather-agent", "adk-agent", "claude-sdk-agent"]

# Agents that use LLM and have API key secrets
_LLM_AGENTS = ["adk-agent", "claude-sdk-agent"]


def _get_pod_name(agent: str, namespace: str) -> str:
    pods = kubectl_get_pods_json(namespace)
    for pod in pods:
        if (
            pod["metadata"]["name"].startswith(agent)
            and pod["status"].get("phase") == "Running"
        ):
            return pod["metadata"]["name"]
    pytest.skip(f"No running pod found for {agent} in {namespace}")


def _kubectl_exec(pod: str, ns: str, cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "exec", pod, "-n", ns, "-c", "agent", "--", *cmd],
        capture_output=True,
        text=True,
        timeout=15,
    )


class TestSecretDelivery:
    """Verify secrets are delivered via K8s secretKeyRef, not hardcoded."""

    @pytest.mark.parametrize("agent", _LLM_AGENTS)
    def test_api_key_from_secret_ref(self, agent, agent_namespace):
        """API key env var must come from a K8s Secret, not a literal value."""
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "deployment",
                agent,
                "-n",
                agent_namespace,
                "-o",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"Deployment {agent} not found")

        dep = json.loads(result.stdout)
        containers = dep["spec"]["template"]["spec"]["containers"]
        agent_container = next((c for c in containers if c["name"] == "agent"), None)
        assert agent_container, f"No 'agent' container in {agent}"

        env_vars = agent_container.get("env", [])
        key_envs = [
            e
            for e in env_vars
            if "API_KEY" in e.get("name", "") or "api_key" in e.get("name", "")
        ]

        for env in key_envs:
            assert "valueFrom" in env, (
                f"{agent}: {env['name']} has literal value instead of secretKeyRef. "
                f"Secrets must be delivered via K8s Secrets."
            )
            assert "secretKeyRef" in env["valueFrom"], (
                f"{agent}: {env['name']} uses valueFrom but not secretKeyRef"
            )


class TestNoHardcodedSecrets:
    """Verify no real API keys are hardcoded in deployment YAML."""

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_no_literal_api_keys(self, agent, agent_namespace):
        """Deployment YAML must not contain literal API key values."""
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "deployment",
                agent,
                "-n",
                agent_namespace,
                "-o",
                "yaml",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"Deployment {agent} not found")

        yaml_text = result.stdout.lower()
        dangerous_patterns = ["sk-", "api_key: sk", "key: ghp_", "key: ghs_"]
        for pattern in dangerous_patterns:
            assert pattern not in yaml_text, (
                f"{agent}: Found potential hardcoded secret pattern '{pattern}' in YAML"
            )


class TestAgentEnvSecurity:
    """Verify agent process environment doesn't leak unnecessary secrets."""

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_no_kubernetes_token_exposed(self, agent, agent_namespace):
        """Agent shouldn't have the K8s service account token in env."""
        pod_name = _get_pod_name(agent, agent_namespace)
        result = _kubectl_exec(pod_name, agent_namespace, ["env"])
        if result.returncode != 0:
            pytest.skip(f"Cannot exec into {agent}: {result.stderr}")

        env_lines = result.stdout.strip().splitlines()
        for line in env_lines:
            assert not line.startswith("KUBERNETES_SERVICE_ACCOUNT_TOKEN="), (
                f"{agent}: K8s SA token found in env (should be file-mounted only)"
            )


class TestPolicyConfigMapMounted:
    """Verify OPA policy ConfigMap is properly mounted in agent pods."""

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_policy_file_exists(self, agent, agent_namespace):
        """The OPA policy file should be mounted at /etc/openshell/."""
        pod_name = _get_pod_name(agent, agent_namespace)
        result = _kubectl_exec(pod_name, agent_namespace, ["ls", "/etc/openshell/"])
        if result.returncode != 0:
            pytest.skip(f"Cannot exec into {agent}: {result.stderr}")

        assert "policy.yaml" in result.stdout, (
            f"{agent}: No policy.yaml found at /etc/openshell/. "
            f"Contents: {result.stdout}"
        )

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_policy_is_valid_yaml(self, agent, agent_namespace):
        """The mounted policy file must be valid YAML with expected fields."""
        pod_name = _get_pod_name(agent, agent_namespace)
        result = _kubectl_exec(
            pod_name, agent_namespace, ["cat", "/etc/openshell/policy.yaml"]
        )
        if result.returncode != 0:
            pytest.skip(f"Cannot read policy: {result.stderr}")

        content = result.stdout
        assert "version:" in content, "Policy missing 'version' field"
        assert "filesystem_policy:" in content or "network_policies:" in content, (
            "Policy missing filesystem or network policy section"
        )


class TestSupervisorIntegration:
    """Tests for when the OpenShell supervisor is the container entrypoint.

    These verify placeholder tokens, supervisor process tree, and
    credential resolution. Currently the supervisor is not yet deployed
    as the entrypoint — these tests document the target state.
    """

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_supervisor_entrypoint_or_document_gap(self, agent, agent_namespace):
        """Check if supervisor is PID 1. If not, document the current state."""
        pod_name = _get_pod_name(agent, agent_namespace)
        result = _kubectl_exec(pod_name, agent_namespace, ["cat", "/proc/1/cmdline"])
        if result.returncode != 0:
            pytest.skip(f"Cannot read /proc/1/cmdline: {result.stderr}")

        cmdline = result.stdout.replace("\x00", " ").strip()
        if "openshell" in cmdline.lower() or "supervisor" in cmdline.lower():
            pass  # Supervisor is entrypoint — test passes
        else:
            pytest.skip(
                f"Supervisor not yet entrypoint for {agent}. "
                f"Current PID 1: {cmdline[:100]}. "
                f"This will pass once supervisor integration is deployed."
            )

    @pytest.mark.parametrize("agent", _LLM_AGENTS)
    def test_placeholder_tokens_or_document_gap(self, agent, agent_namespace):
        """Check if env vars use openshell:resolve:env: placeholders."""
        pod_name = _get_pod_name(agent, agent_namespace)
        result = _kubectl_exec(pod_name, agent_namespace, ["env"])
        if result.returncode != 0:
            pytest.skip(f"Cannot exec into {agent}: {result.stderr}")

        env_lines = result.stdout.strip().splitlines()
        has_placeholder = any("openshell:resolve:env:" in line for line in env_lines)
        if has_placeholder:
            pass  # Placeholders present — test passes
        else:
            pytest.skip(
                f"Placeholder tokens not yet in {agent} env. "
                f"This will pass once supervisor integration is deployed."
            )
