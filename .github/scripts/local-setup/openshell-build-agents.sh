#!/usr/bin/env bash
#
# Build and load OpenShell PoC agent images into the cluster.
# Idempotent — skips images that already exist in the cluster.
#
# Called by openshell-full-test.sh during the agents-deploy phase.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
AGENTS_DIR="$REPO_ROOT/deployments/openshell/agents"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
log_step()  { echo -e "${GREEN}>>>${NC} $1"; }
log_skip()  { echo -e "${YELLOW}>>>${NC} $1 (already exists)"; }

# Detect cluster type
CLUSTER_TYPE="${PLATFORM:-kind}"
CLUSTER_NAME="${CLUSTER_NAME:-kagenti}"

build_and_load() {
    local name="$1"
    local dir="$2"

    # Check if image already loaded in cluster
    if [ "$CLUSTER_TYPE" = "kind" ]; then
        if docker exec "${CLUSTER_NAME}-control-plane" crictl images 2>/dev/null | grep -q "$name"; then
            log_skip "Image $name:latest"
            return 0
        fi
    fi

    # Build
    if [ -f "$dir/Dockerfile" ]; then
        log_step "Building $name..."
        docker build -t "$name:latest" "$dir" -q
    else
        log_skip "No Dockerfile for $name"
        return 0
    fi

    # Load into cluster
    if [ "$CLUSTER_TYPE" = "kind" ]; then
        log_step "Loading $name into Kind..."
        kind load docker-image "$name:latest" --name "$CLUSTER_NAME" 2>/dev/null
    fi
}

# Build custom agents and create policy ConfigMaps
AGENT_NS="${AGENT_NS:-team1}"
for agent_dir in "$AGENTS_DIR"/*/; do
    [ -d "$agent_dir" ] || continue
    agent_name=$(basename "$agent_dir")

    # Build image if Dockerfile exists
    if [ -f "$agent_dir/Dockerfile" ]; then
        build_and_load "$agent_name" "$agent_dir"
    fi

    # Create policy ConfigMap if both policy files exist (idempotent)
    if [ -f "$agent_dir/policy-data.yaml" ] && [ -f "$agent_dir/sandbox-policy.rego" ]; then
        cm_name="${agent_name}-policy"
        if kubectl get configmap "$cm_name" -n "$AGENT_NS" >/dev/null 2>&1; then
            log_skip "ConfigMap $cm_name"
        else
            log_step "Creating ConfigMap $cm_name..."
            kubectl create configmap "$cm_name" -n "$AGENT_NS" \
                --from-file=policy.yaml="$agent_dir/policy-data.yaml" \
                --from-file=sandbox-policy.rego="$agent_dir/sandbox-policy.rego"
        fi
    fi
done

log_step "All agent images built and loaded."
