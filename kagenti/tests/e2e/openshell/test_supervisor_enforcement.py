"""
OpenShell Supervisor Enforcement Tests

Tests that verify the supervisor ACTUALLY enforces isolation:
- Landlock blocks filesystem writes outside allowed paths
- Network namespace isolates the agent from direct external access
- OPA proxy is the only network exit point
- Seccomp filters are applied

These tests verify enforcement by checking supervisor logs for
applied rules (since kubectl exec bypasses per-process restrictions).
For live enforcement tests, the agent process itself would need to
attempt violations — which we test via the A2A endpoint where possible.
"""

import json
import os
import subprocess
import re

import httpx
import pytest

from kagenti.tests.e2e.openshell.conftest import a2a_send, extract_a2a_text

pytestmark = [pytest.mark.openshell]

GATEWAY_NS = os.getenv("OPENSHELL_GATEWAY_NAMESPACE", "openshell-system")
AGENT_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")
SUPERVISED_AGENT = "weather-agent-supervised"


from kagenti.tests.e2e.openshell.conftest import kubectl_run


def _kubectl(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    return kubectl_run(*args, timeout=timeout)


def _get_supervisor_logs() -> str:
    result = _kubectl(
        "logs",
        f"deploy/{SUPERVISED_AGENT}",
        "-n",
        AGENT_NS,
        "-c",
        "agent",
    )
    return result.stdout if result.returncode == 0 else ""


def _supervised_pod_exists() -> bool:
    result = _kubectl(
        "get",
        "deploy",
        SUPERVISED_AGENT,
        "-n",
        AGENT_NS,
        "-o",
        "jsonpath={.status.readyReplicas}",
    )
    return result.stdout.strip() == "1"


skip_no_supervised = pytest.mark.skipif(
    not _supervised_pod_exists(),
    reason=f"{SUPERVISED_AGENT} not deployed",
)


class TestLandlockEnforcement:
    """Verify Landlock filesystem sandbox is applied."""

    @skip_no_supervised
    def test_landlock_applied_in_logs(self):
        """Supervisor logs must show Landlock was applied with rules."""
        logs = _get_supervisor_logs()
        assert "CONFIG:APPLYING" in logs, "No Landlock application in logs"
        assert "Landlock filesystem sandbox" in logs
        assert "rules_applied:" in logs

        match = re.search(r"rules_applied:(\d+)", logs)
        assert match, "Cannot parse rules_applied count"
        rules = int(match.group(1))
        assert rules >= 10, f"Only {rules} Landlock rules — expected 10+"

    @skip_no_supervised
    def test_landlock_abi_version(self):
        """Supervisor must use Landlock ABI V2 or higher."""
        logs = _get_supervisor_logs()
        assert "abi:" in logs.lower()
        match = re.search(r"abi:v(\d+)", logs, re.IGNORECASE)
        assert match, "Cannot parse Landlock ABI version"
        version = int(match.group(1))
        assert version >= 2, f"Landlock ABI v{version} too old (need v2+)"

    @skip_no_supervised
    def test_read_only_paths_configured(self):
        """Policy must define read-only paths."""
        result = _kubectl(
            "get",
            "configmap",
            f"{SUPERVISED_AGENT}-policy",
            "-n",
            AGENT_NS,
            "-o",
            "jsonpath={.data.policy\\.yaml}",
        )
        assert result.returncode == 0
        assert "read_only:" in result.stdout
        assert "/usr" in result.stdout
        assert "/etc" in result.stdout

    @skip_no_supervised
    def test_read_write_paths_configured(self):
        """Policy must define read-write paths (tmp, app)."""
        result = _kubectl(
            "get",
            "configmap",
            f"{SUPERVISED_AGENT}-policy",
            "-n",
            AGENT_NS,
            "-o",
            "jsonpath={.data.policy\\.yaml}",
        )
        assert "/tmp" in result.stdout
        assert "/app" in result.stdout


class TestNetworkNamespaceEnforcement:
    """Verify network namespace isolation is applied."""

    @skip_no_supervised
    def test_netns_created_in_logs(self):
        """Supervisor logs must show network namespace was created."""
        logs = _get_supervisor_logs()
        assert "CONFIG:CREATING" in logs
        assert "Network namespace" in logs
        assert "10.200.0.1" in logs, "Host veth IP not found"
        assert "10.200.0.2" in logs, "Sandbox veth IP not found"

    @skip_no_supervised
    def test_opa_proxy_listening(self):
        """OPA proxy must be listening on 10.200.0.1:3128."""
        logs = _get_supervisor_logs()
        assert "NET:LISTEN" in logs
        assert "10.200.0.1:3128" in logs

    @skip_no_supervised
    def test_netns_name_in_logs(self):
        """Network namespace must have a unique name."""
        logs = _get_supervisor_logs()
        match = re.search(r"ns:sandbox-([a-f0-9]+)", logs)
        assert match, "No sandbox netns name in logs"
        ns_id = match.group(1)
        assert len(ns_id) >= 6, f"Netns ID too short: {ns_id}"


class TestSeccompEnforcement:
    """Verify seccomp syscall filtering is applied."""

    @skip_no_supervised
    def test_seccomp_not_explicitly_disabled(self):
        """Pod spec must not have seccomp set to Unconfined."""
        result = _kubectl(
            "get",
            f"deploy/{SUPERVISED_AGENT}",
            "-n",
            AGENT_NS,
            "-o",
            "json",
        )
        dep = json.loads(result.stdout)
        containers = dep["spec"]["template"]["spec"]["containers"]
        for c in containers:
            sc = c.get("securityContext", {})
            seccomp = sc.get("seccompProfile", {})
            assert seccomp.get("type") != "Unconfined", (
                f"Container {c['name']} has seccomp Unconfined"
            )


class TestOPAPolicyEnforcement:
    """Verify OPA policy is loaded and evaluating."""

    @skip_no_supervised
    def test_opa_policy_loaded(self):
        """Supervisor logs must show OPA policy was loaded."""
        logs = _get_supervisor_logs()
        assert "CONFIG:LOADING" in logs
        assert "OPA policy engine" in logs
        assert "sandbox-policy.rego" in logs

    @skip_no_supervised
    def test_policy_has_network_rules(self):
        """OPA policy data must define network endpoint rules."""
        result = _kubectl(
            "get",
            "configmap",
            f"{SUPERVISED_AGENT}-policy",
            "-n",
            AGENT_NS,
            "-o",
            "jsonpath={.data.policy\\.yaml}",
        )
        assert "network_policies:" in result.stdout
        assert "endpoints:" in result.stdout

    @skip_no_supervised
    def test_rego_file_mounted(self):
        """The OPA Rego rules file must be mounted in the pod."""
        result = _kubectl(
            "exec",
            f"deploy/{SUPERVISED_AGENT}",
            "-n",
            AGENT_NS,
            "--",
            "ls",
            "/etc/openshell/sandbox-policy.rego",
        )
        assert result.returncode == 0, "Rego policy file not mounted"

    @skip_no_supervised
    def test_tls_termination_enabled(self):
        """Supervisor must enable TLS termination for L7 inspection."""
        logs = _get_supervisor_logs()
        assert "TLS termination enabled" in logs
        assert "ephemeral CA generated" in logs


class TestRealGitHubPRReview:
    """Test agent reviewing a real GitHub PR diff.

    Fetches a real PR diff from GitHub and sends it to the agent
    for review. Requires OPENSHELL_LLM_AVAILABLE=true and a
    GitHub token (from GITHUB_TOKEN env or .env.kagenti).
    """

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() != "true",
        reason="LLM not available",
    )
    async def test_review_real_github_pr(self, claude_sdk_agent_url):
        """Fetch a real PR diff from kagenti repo and review it."""
        gh_token = os.getenv("GITHUB_TOKEN", "")

        # Fetch a recent PR diff from kagenti repo
        headers = {"Accept": "application/vnd.github.v3.diff"}
        if gh_token:
            headers["Authorization"] = f"token {gh_token}"

        async with httpx.AsyncClient() as client:
            # Use a known small PR for predictable testing
            diff_resp = await client.get(
                "https://api.github.com/repos/kagenti/kagenti/pulls/1300",
                headers={**headers, "Accept": "application/vnd.github.v3.diff"},
                timeout=15.0,
            )

        if diff_resp.status_code != 200:
            pytest.skip(f"Cannot fetch PR diff: HTTP {diff_resp.status_code}")

        diff_text = diff_resp.text[:2000]  # First 2KB to keep prompt short

        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                f"Review this pull request diff for security and code quality:\n\n```diff\n{diff_text}\n```",
                request_id="github-pr-review",
                timeout=120.0,
            )

        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text, "Empty review response"
        assert len(text) > 50, f"Review too short: {text[:100]}"

    @pytest.mark.asyncio
    @pytest.mark.skipif(
        os.getenv("OPENSHELL_LLM_AVAILABLE", "").lower() != "true",
        reason="LLM not available",
    )
    async def test_rca_style_log_analysis(self, claude_sdk_agent_url):
        """Send CI-style error logs and ask agent for root cause analysis."""
        fake_ci_logs = """
[2026-04-22T10:00:00Z] Starting E2E test suite...
[2026-04-22T10:00:05Z] test_platform_health PASSED
[2026-04-22T10:00:10Z] test_agent_deploy PASSED
[2026-04-22T10:00:15Z] test_weather_agent FAILED
[2026-04-22T10:00:15Z] ERROR: Connection refused: weather-agent.team1.svc:8080
[2026-04-22T10:00:15Z] TRACEBACK: httpx.ConnectError: All connection attempts failed
[2026-04-22T10:00:16Z] test_adk_agent FAILED
[2026-04-22T10:00:16Z] ERROR: Connection refused: adk-agent.team1.svc:8080
[2026-04-22T10:00:17Z] Pod weather-agent-abc123: CrashLoopBackOff (3 restarts)
[2026-04-22T10:00:17Z] Pod adk-agent-def456: CreateContainerConfigError
[2026-04-22T10:00:17Z] Events: secret "litellm-virtual-keys" not found
"""
        async with httpx.AsyncClient() as client:
            resp = await a2a_send(
                client,
                claude_sdk_agent_url,
                f"Analyze these CI logs and identify the root cause of the failures:\n\n```\n{fake_ci_logs}\n```",
                request_id="rca-logs",
                timeout=120.0,
            )

        assert "result" in resp
        text = extract_a2a_text(resp)
        assert text, "Empty RCA response"
        text_lower = text.lower()
        has_rca = any(
            kw in text_lower
            for kw in ["secret", "litellm", "missing", "not found", "root cause", "fix"]
        )
        assert has_rca, f"Response doesn't identify root cause: {text[:200]}"
