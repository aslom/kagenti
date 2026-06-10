#!/usr/bin/env python3
"""Sync all kagenti-teleport-setup files with the remote server pod.

Single script that handles the full lifecycle:
1. Check remote pod is available (health endpoint)
2. Compare all local files with remote using HTTP ETags (RFC 9110 Section 8.8)
3. If files differ: update ConfigMap, restart deployment
4. Verify all updates are reflected in the remote pod

Uses /checksums endpoint for efficient bulk comparison (one request returns
all file ETags). Individual files can also be checked via HEAD + If-None-Match
which returns 304 Not Modified if the file hasn't changed.

Usage:
    uv run kagenti/scripts/sync-kagenti-teleport-setup.py           # Sync if needed
    uv run kagenti/scripts/sync-kagenti-teleport-setup.py --status  # Check only
    uv run kagenti/scripts/sync-kagenti-teleport-setup.py --deploy  # Initial deploy
    uv run kagenti/scripts/sync-kagenti-teleport-setup.py --force   # Force redeploy
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import urllib.error

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
K8S_DIR = SCRIPT_DIR / "k8s"
SERVER_YAML = K8S_DIR / "kagenti-teleport-setup-server.yaml"
INDEX_HTML = K8S_DIR / "index.html"

NAMESPACE = "team1"
CONFIGMAP_NAME = "kagenti-teleport-setup"
DEPLOYMENT_NAME = "kagenti-teleport-setup"
ROUTE_NAME = "kagenti-teleport-setup"

DEFAULT_ROUTE_URL = "https://kagenti-teleport-setup-team1.apps.epoc002.ete14.res.ibm.com"

SERVED_FILES = [
    "kagenti-teleport-setup.py",
    "kosh.py",
    "teleport.sh",
    "sandbox.sh",
    "litellm_sandbox_policy.yaml",
    "setup.sh",
    "index.html",
]


# ---------------------------------------------------------------------------
# Infrastructure helpers
# ---------------------------------------------------------------------------

def find_kubeconfig() -> str:
    if os.environ.get("KUBECONFIG"):
        return os.environ["KUBECONFIG"]
    repo_root = SCRIPT_DIR.parent.parent
    epoc_config = repo_root / ".kube" / "config-epoc"
    if epoc_config.exists():
        return str(epoc_config)
    home_config = pathlib.Path.home() / ".kube" / "config-epoc"
    if home_config.exists():
        return str(home_config)
    print("ERROR: Cannot find kubeconfig for EPOC cluster.", file=sys.stderr)
    print("  Set KUBECONFIG env var or place config at .kube/config-epoc", file=sys.stderr)
    sys.exit(1)


def find_kubectl() -> str:
    kubectl = shutil.which("kubectl") or shutil.which("oc")
    if not kubectl:
        print("ERROR: kubectl/oc not found in PATH", file=sys.stderr)
        sys.exit(1)
    return kubectl


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# File resolution
# ---------------------------------------------------------------------------

def local_file_path(filename: str) -> pathlib.Path | None:
    """Resolve local path for a served filename."""
    if filename == "index.html":
        return INDEX_HTML if INDEX_HTML.exists() else None
    if filename == "setup.sh":
        p = K8S_DIR / "setup.sh"
        return p if p.exists() else None
    candidate = SCRIPT_DIR / filename
    return candidate if candidate.exists() else None


def compute_etag(filepath: pathlib.Path) -> str:
    """Compute strong ETag as quoted SHA-256 hex (RFC 9110 Section 8.8.3)."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f'"{h.hexdigest()}"'


# ---------------------------------------------------------------------------
# Step 1: Check remote pod is available
# ---------------------------------------------------------------------------

def check_pod_health(route_url: str) -> bool:
    """Check that the remote pod is reachable via /health endpoint."""
    url = f"{route_url}/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ssl_context(), timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def get_route_url(kubectl: str, kubeconfig: str) -> str:
    result = subprocess.run(
        [kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}",
         "get", "route", ROUTE_NAME, "-o", "jsonpath={.spec.host}"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode == 0 and result.stdout:
        return f"https://{result.stdout}"
    return DEFAULT_ROUTE_URL


# ---------------------------------------------------------------------------
# Step 2: Compare files using HTTP ETags
# ---------------------------------------------------------------------------

def fetch_checksums(route_url: str) -> dict[str, str] | None:
    """GET /checksums → {filename: ETag}. Returns None if unreachable."""
    url = f"{route_url}/checksums"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ssl_context(), timeout=15) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"  /checksums unavailable: {e}", file=sys.stderr)
        return None


def compare_all(route_url: str) -> tuple[list[str], list[str], list[str]]:
    """Compare all served files via /checksums.

    Returns (changed, missing_remote, missing_local).
    """
    remote_checksums = fetch_checksums(route_url)

    changed: list[str] = []
    missing_remote: list[str] = []
    missing_local: list[str] = []

    if remote_checksums is None:
        for f in SERVED_FILES:
            if local_file_path(f):
                changed.append(f)
        return changed, missing_remote, missing_local

    for filename in SERVED_FILES:
        lpath = local_file_path(filename)
        if lpath is None:
            missing_local.append(filename)
            continue

        local_etag = compute_etag(lpath)
        remote_etag = remote_checksums.get(filename)

        if remote_etag is None:
            missing_remote.append(filename)
        elif local_etag != remote_etag:
            changed.append(filename)

    return changed, missing_remote, missing_local


# ---------------------------------------------------------------------------
# Step 3: Update ConfigMap + restart deployment
# ---------------------------------------------------------------------------

def update_configmap(kubectl: str, kubeconfig: str) -> bool:
    """Update content ConfigMap from all local served files."""
    print("  Updating ConfigMap...")

    from_file_args = []
    for filename in SERVED_FILES:
        lpath = local_file_path(filename)
        if lpath:
            from_file_args.append(f"--from-file={filename}={lpath}")

    if not from_file_args:
        print("  ERROR: No local files found", file=sys.stderr)
        return False

    cmd = [
        kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}",
        "create", "configmap", CONFIGMAP_NAME,
    ] + from_file_args + ["--dry-run=client", "-o", "yaml"]

    create_result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if create_result.returncode != 0:
        print(f"  ERROR: {create_result.stderr}", file=sys.stderr)
        return False

    apply_result = subprocess.run(
        [kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}", "apply", "-f", "-"],
        input=create_result.stdout, capture_output=True, text=True, timeout=30,
    )
    if apply_result.returncode != 0:
        print(f"  ERROR: {apply_result.stderr}", file=sys.stderr)
        return False

    print(f"  ConfigMap updated ({len(from_file_args)} files).")
    return True


def restart_deployment(kubectl: str, kubeconfig: str) -> bool:
    print("  Restarting deployment...")
    result = subprocess.run(
        [kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}",
         "rollout", "restart", f"deployment/{DEPLOYMENT_NAME}"],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}", file=sys.stderr)
        return False

    print("  Waiting for rollout...")
    result = subprocess.run(
        [kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}",
         "rollout", "status", f"deployment/{DEPLOYMENT_NAME}", "--timeout=90s"],
        capture_output=True, text=True, timeout=100,
    )
    if result.returncode != 0:
        print(f"  WARNING: Rollout status: {result.stderr.strip()}", file=sys.stderr)
    else:
        print("  Deployment restarted.")
    return True


def deploy_all(kubectl: str, kubeconfig: str) -> bool:
    """Initial deployment: apply server YAML + create content ConfigMap."""
    print("=== Initial Deployment ===\n")

    if not SERVER_YAML.exists():
        print(f"ERROR: {SERVER_YAML} not found", file=sys.stderr)
        return False

    print("  Applying server manifests...")
    result = subprocess.run(
        [kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}",
         "apply", "-f", str(SERVER_YAML)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}", file=sys.stderr)
        return False
    print("  Server manifests applied.")

    if not update_configmap(kubectl, kubeconfig):
        return False

    print("  Waiting for rollout...")
    subprocess.run(
        [kubectl, f"--kubeconfig={kubeconfig}", f"--namespace={NAMESPACE}",
         "rollout", "status", f"deployment/{DEPLOYMENT_NAME}", "--timeout=90s"],
        capture_output=True, text=True, timeout=100,
    )
    return True


# ---------------------------------------------------------------------------
# Step 4: Verify all updates are reflected in remote pod
# ---------------------------------------------------------------------------

def verify_sync(route_url: str, retries: int = 5) -> bool:
    """Verify all files match using /checksums after deployment."""
    print("\n=== Verification ===\n")
    print("  Checking remote pod reflects all updates...")
    for attempt in range(retries):
        time.sleep(2)

        if not check_pod_health(route_url):
            print(f"    Attempt {attempt + 1}/{retries}: pod not ready...")
            continue

        changed, missing_remote, _ = compare_all(route_url)
        if not changed and not missing_remote:
            print("  VERIFIED: All remote files match local (ETags identical).")
            return True

        remaining = changed + missing_remote
        print(f"    Attempt {attempt + 1}/{retries}: {len(remaining)} file(s) still differ...")

    print("  ERROR: Verification failed — remote does not match local", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def print_status(route_url: str) -> bool:
    """Print detailed sync status. Returns True if all in sync."""
    print("=== File Sync Status ===\n")
    print(f"  Route: {route_url}")
    print(f"  Method: HTTP ETag (RFC 9110 Section 8.8)\n")

    # Pod health
    healthy = check_pod_health(route_url)
    print(f"  Pod health: {'OK' if healthy else 'UNREACHABLE'}")
    if not healthy:
        print("  Cannot compare files — pod not reachable.")
        return False

    remote_checksums = fetch_checksums(route_url)
    if remote_checksums is None:
        print("  /checksums endpoint not available (old server version?).")
        return False

    print(f"\n  {'File':<35} {'Local ETag':<16} {'Remote ETag':<16} {'Status'}")
    print(f"  {'-'*35} {'-'*16} {'-'*16} {'-'*10}")

    all_synced = True
    for filename in SERVED_FILES:
        lpath = local_file_path(filename)
        if lpath is None:
            remote_short = remote_checksums.get(filename, "(missing)")
            if isinstance(remote_short, str) and len(remote_short) > 14:
                remote_short = remote_short[1:13] + "..."
            print(f"  {filename:<35} {'(no local)':<16} {remote_short:<16} SKIP")
            continue

        local_etag = compute_etag(lpath)
        remote_etag = remote_checksums.get(filename)

        local_short = local_etag[1:13] + "..."
        if remote_etag is None:
            remote_short = "(missing)"
            status = "MISSING"
            all_synced = False
        elif local_etag == remote_etag:
            remote_short = remote_etag[1:13] + "..."
            status = "OK"
        else:
            remote_short = remote_etag[1:13] + "..."
            status = "CHANGED"
            all_synced = False

        print(f"  {filename:<35} {local_short:<16} {remote_short:<16} {status}")

    print(f"\n  Overall: {'ALL IN SYNC' if all_synced else 'OUT OF SYNC'}")
    return all_synced


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync kagenti-teleport-setup files with remote pod (HTTP ETag comparison)",
    )
    parser.add_argument("--deploy", action="store_true",
                        help="Initial deploy (all manifests + ConfigMap)")
    parser.add_argument("--status", action="store_true",
                        help="Check sync status only (no changes)")
    parser.add_argument("--force", action="store_true",
                        help="Redeploy even if all files match")
    parser.add_argument("--url", default=None,
                        help="Override route URL")
    args = parser.parse_args()

    kubeconfig = find_kubeconfig()
    kubectl = find_kubectl()
    route_url = args.url or get_route_url(kubectl, kubeconfig)

    print(f"  KUBECONFIG: {kubeconfig}")
    print(f"  Route URL: {route_url}\n")

    # --- Initial deploy ---
    if args.deploy:
        if not deploy_all(kubectl, kubeconfig):
            return 1
        if not verify_sync(route_url):
            return 1
        print(f"\n  DEPLOYED. URL: {route_url}/kagenti-teleport-setup.py")
        return 0

    # --- Step 1: Check pod availability ---
    print("=== Step 1: Check Remote Pod ===\n")
    healthy = check_pod_health(route_url)
    if healthy:
        print("  Pod is healthy.")
    else:
        print("  Pod not reachable.")
        if args.status:
            print("  Run with --deploy for initial setup.")
            return 1
        print("  Attempting to deploy...")
        if not deploy_all(kubectl, kubeconfig):
            return 1
        if not verify_sync(route_url):
            return 1
        print(f"\n  DEPLOYED. URL: {route_url}/kagenti-teleport-setup.py")
        return 0

    # --- Step 2: Compare files ---
    print("\n=== Step 2: Compare Files (HTTP ETag) ===\n")
    changed, missing_remote, missing_local = compare_all(route_url)

    if missing_local:
        print(f"  Skipping (no local file): {', '.join(missing_local)}")
    if not changed and not missing_remote:
        if args.force:
            print("  All files match, but --force specified.")
        else:
            print("  ALL IN SYNC. Nothing to do.")
            if args.status:
                print()
                print_status(route_url)
            else:
                print(f"  URL: {route_url}/kagenti-teleport-setup.py")
            return 0

    if changed:
        print(f"  Changed: {', '.join(changed)}")
    if missing_remote:
        print(f"  Missing on remote: {', '.join(missing_remote)}")

    if args.status:
        print("\n  Run without --status to sync.")
        return 1

    # --- Step 3: Update ---
    print("\n=== Step 3: Update Remote Pod ===\n")
    if not update_configmap(kubectl, kubeconfig):
        return 1
    if not restart_deployment(kubectl, kubeconfig):
        return 1

    # --- Step 4: Verify ---
    if not verify_sync(route_url):
        return 1

    print(f"\n  DONE. URL: {route_url}/kagenti-teleport-setup.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
