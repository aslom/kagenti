"""
Tests for OpenShell built-in sandbox agents (Mode 2).

These tests create sandboxes using the OpenShell gateway and the pre-installed
CLI agents in the base image (ghcr.io/nvidia/openshell-community/sandboxes/base).

Each test verifies:
1. Sandbox can be created via the gateway
2. The CLI agent responds to a basic command
3. Sandbox can be destroyed

Tests are skipped when:
- OpenShell gateway is not deployed
- Required API keys are not available
- The base sandbox image is not pulled
"""

import json
import os
import subprocess

import pytest

pytestmark = [pytest.mark.openshell, pytest.mark.asyncio]


GATEWAY_NS = os.getenv("OPENSHELL_GATEWAY_NAMESPACE", "openshell-system")
AGENT_NS = os.getenv("OPENSHELL_AGENT_NAMESPACE", "team1")


def _gateway_available() -> bool:
    """Check if the OpenShell gateway pod is running."""
    try:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                GATEWAY_NS,
                "-l",
                "app.kubernetes.io/name=openshell-gateway",
                "-o",
                "jsonpath={.items[0].status.phase}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() == "Running"
    except Exception:
        return False


def _exec_in_gateway(cmd: str) -> tuple[int, str, str]:
    """Execute a command inside the gateway pod."""
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            GATEWAY_NS,
            "openshell-gateway-0",
            "--",
            "sh",
            "-c",
            cmd,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.returncode, result.stdout, result.stderr


skip_no_gateway = pytest.mark.skipif(
    not _gateway_available(),
    reason="OpenShell gateway not running",
)


class TestClaudeSandbox:
    """Test creating a sandbox with Claude CLI (pre-installed in base image).

    Requires: Anthropic API key or LiteLLM virtual key configured as
    an OpenShell provider. OpenShell's inference router will route
    inference.local to the configured LLM backend.

    TODO: Configure OpenShell provider for Claude/Anthropic so the
    inference router can inject the API key. Currently the sandbox
    will start but Claude CLI won't be able to make LLM calls without
    a configured provider.
    """

    @skip_no_gateway
    @pytest.mark.skip(
        reason="TODO: Requires OpenShell provider configuration for Anthropic. "
        "Claude CLI needs ANTHROPIC_API_KEY via inference router."
    )
    def test_claude_sandbox_create_and_respond(self):
        """Create a Claude sandbox, send a message, verify response."""
        pass


class TestOpenCodeSandbox:
    """Test creating a sandbox with OpenCode CLI (pre-installed in base image).

    Requires: OpenAI-compatible API key configured as an OpenShell provider.
    OpenCode uses the OpenAI chat completions format natively, which
    LiteLLM supports.

    TODO: Configure OpenShell provider for OpenAI-compatible endpoint.
    OpenCode reads OPENAI_API_KEY from environment — the supervisor
    proxy will resolve the placeholder token.
    """

    @skip_no_gateway
    @pytest.mark.skip(
        reason="TODO: Requires OpenShell provider configuration for OpenAI. "
        "OpenCode needs OPENAI_API_KEY via inference router."
    )
    def test_opencode_sandbox_create_and_respond(self):
        """Create an OpenCode sandbox, send a command, verify response."""
        pass


class TestCodexSandbox:
    """Test creating a sandbox with Codex CLI (pre-installed in base image).

    Requires: Real OpenAI API key — Codex uses OpenAI-specific endpoints
    that may not be fully compatible with LiteLLM translation.

    SKIP: Codex requires a real OpenAI API key. LiteLLM's OpenAI
    translation may not cover all Codex-specific API calls.
    """

    @skip_no_gateway
    @pytest.mark.skip(
        reason="Codex requires real OpenAI API key. "
        "LiteLLM translation may not cover Codex-specific API endpoints."
    )
    def test_codex_sandbox_create_and_respond(self):
        """Create a Codex sandbox — requires OpenAI API key."""
        pass


class TestCopilotSandbox:
    """Test creating a sandbox with GitHub Copilot CLI (pre-installed).

    SKIP: Copilot uses GitHub's proprietary Copilot API which is not
    compatible with LiteLLM or any open model routing. Requires an
    active GitHub Copilot subscription token.
    """

    @skip_no_gateway
    @pytest.mark.skip(
        reason="Copilot requires GitHub Copilot subscription. "
        "Not compatible with LiteLLM — uses proprietary GitHub API."
    )
    def test_copilot_sandbox_create_and_respond(self):
        """Create a Copilot sandbox — requires GitHub Copilot subscription."""
        pass


class TestBaseSandboxImage:
    """Test that the OpenShell base sandbox image has expected CLIs.

    This test doesn't need any API keys — it just verifies the image
    contents by creating a sandbox and checking binary availability.
    """

    @skip_no_gateway
    @pytest.mark.skip(
        reason="TODO: Implement sandbox creation via gateway gRPC/exec. "
        "Need to configure provider before sandbox creation works."
    )
    def test_base_image_has_expected_binaries(self):
        """Create a sandbox and verify claude, opencode, codex, copilot are on PATH."""
        pass
