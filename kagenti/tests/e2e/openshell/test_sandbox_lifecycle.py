"""
Sandbox Lifecycle E2E Tests (OpenShell PoC)

Tests sandbox create / list / delete operations via the OpenShell Gateway.

The gateway exposes a gRPC API on port 8080.  Since the Python gRPC client
may not be available in every test environment, these tests fall back to
running ``kubectl exec`` into the gateway pod to exercise the CLI.

Usage:
    pytest kagenti/tests/e2e/openshell/test_sandbox_lifecycle.py -v -m openshell
"""

import json
import subprocess

import pytest

from kagenti.tests.e2e.openshell.conftest import kubectl_get_pods_json


pytestmark = pytest.mark.openshell

# Unique sandbox name for this test run to avoid collisions
_TEST_SANDBOX_NAME = "e2e-test-sandbox"


def _gateway_pod_name(gateway_namespace: str) -> str:
    """Return the name of the first running gateway pod, or skip."""
    pods = kubectl_get_pods_json(gateway_namespace)
    for pod in pods:
        if (
            pod["metadata"]["name"].startswith("openshell-gateway")
            and pod["status"].get("phase") == "Running"
        ):
            return pod["metadata"]["name"]
    pytest.skip("No running openshell-gateway pod found")


def _kubectl_exec(
    pod_name: str, namespace: str, command: list[str]
) -> subprocess.CompletedProcess:
    """Run a command inside a pod via ``kubectl exec``."""
    cmd = [
        "kubectl",
        "exec",
        pod_name,
        "-n",
        namespace,
        "--",
        *command,
    ]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class TestSandboxLifecycle:
    """Test sandbox CRUD operations via kubectl exec into the gateway pod."""

    def test_list_sandboxes(self, gateway_namespace):
        """List sandboxes -- should succeed even if none exist yet."""
        pod = _gateway_pod_name(gateway_namespace)
        result = _kubectl_exec(
            pod,
            gateway_namespace,
            ["openshell", "sandbox", "list", "--output", "json"],
        )

        if result.returncode != 0:
            # The CLI may not be available inside the gateway image yet
            if "executable file not found" in result.stderr:
                pytest.skip("openshell CLI not available in gateway pod")
            pytest.fail(
                f"sandbox list failed (rc={result.returncode}): {result.stderr}"
            )

        # Should return valid JSON (possibly an empty list)
        data = json.loads(result.stdout)
        assert isinstance(data, (list, dict)), (
            f"Unexpected sandbox list output: {result.stdout[:200]}"
        )

    def test_create_sandbox(self, gateway_namespace):
        """Create a sandbox and verify it appears in the list."""
        pod = _gateway_pod_name(gateway_namespace)

        # Create
        result = _kubectl_exec(
            pod,
            gateway_namespace,
            [
                "openshell",
                "sandbox",
                "create",
                "--name",
                _TEST_SANDBOX_NAME,
                "--output",
                "json",
            ],
        )

        if result.returncode != 0:
            if "executable file not found" in result.stderr:
                pytest.skip("openshell CLI not available in gateway pod")
            pytest.fail(
                f"sandbox create failed (rc={result.returncode}): {result.stderr}"
            )

        # Verify via list
        list_result = _kubectl_exec(
            pod,
            gateway_namespace,
            ["openshell", "sandbox", "list", "--output", "json"],
        )
        assert list_result.returncode == 0, (
            f"sandbox list after create failed: {list_result.stderr}"
        )

        data = json.loads(list_result.stdout)
        # Accept both list-of-dicts and dict-with-items
        items = (
            data
            if isinstance(data, list)
            else data.get("items", data.get("sandboxes", []))
        )

        sandbox_names = [
            s.get("name", s.get("metadata", {}).get("name", "")) for s in items
        ]
        assert _TEST_SANDBOX_NAME in sandbox_names, (
            f"Created sandbox '{_TEST_SANDBOX_NAME}' not in list: {sandbox_names}"
        )

    def test_delete_sandbox(self, gateway_namespace):
        """Delete the test sandbox and verify it is gone."""
        pod = _gateway_pod_name(gateway_namespace)

        result = _kubectl_exec(
            pod,
            gateway_namespace,
            [
                "openshell",
                "sandbox",
                "delete",
                "--name",
                _TEST_SANDBOX_NAME,
            ],
        )

        if result.returncode != 0:
            if "executable file not found" in result.stderr:
                pytest.skip("openshell CLI not available in gateway pod")
            # Sandbox may not exist if create was skipped -- not a hard failure
            if "not found" in result.stderr.lower():
                pytest.skip(
                    "Sandbox was not created (create test may have been skipped)"
                )
            pytest.fail(
                f"sandbox delete failed (rc={result.returncode}): {result.stderr}"
            )

        # Verify removal
        list_result = _kubectl_exec(
            pod,
            gateway_namespace,
            ["openshell", "sandbox", "list", "--output", "json"],
        )
        if list_result.returncode == 0:
            data = json.loads(list_result.stdout)
            items = (
                data
                if isinstance(data, list)
                else data.get("items", data.get("sandboxes", []))
            )
            sandbox_names = [
                s.get("name", s.get("metadata", {}).get("name", "")) for s in items
            ]
            assert _TEST_SANDBOX_NAME not in sandbox_names, (
                f"Sandbox '{_TEST_SANDBOX_NAME}' still present after delete"
            )
