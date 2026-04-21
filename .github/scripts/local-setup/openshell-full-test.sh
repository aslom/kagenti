#!/usr/bin/env bash
#
# OpenShell PoC Full Test
#
# Creates a Kind cluster, installs Kagenti (headless), deploys OpenShell
# Gateway + agents, and runs E2E tests.
#
# USAGE:
#   ./.github/scripts/local-setup/openshell-full-test.sh [options]
#
# OPTIONS:
#   --skip-cluster-create   Reuse existing Kind cluster
#   --skip-cluster-destroy  Keep cluster after test (for debugging)
#   --skip-test             Skip E2E test phase
#   --skip-agents           Skip agent deployment
#   --cluster-name NAME     Kind cluster name (default: kagenti)
#
# EXAMPLES:
#   # Full run
#   ./.github/scripts/local-setup/openshell-full-test.sh
#
#   # Keep cluster for debugging
#   ./.github/scripts/local-setup/openshell-full-test.sh --skip-cluster-destroy
#
#   # Iterate on existing cluster
#   ./.github/scripts/local-setup/openshell-full-test.sh --skip-cluster-create --skip-cluster-destroy
#

set -euo pipefail

# Handle Ctrl+C — kill child processes only
cleanup() {
    echo ""
    echo -e "\033[0;31mInterrupted — killing child processes...\033[0m"
    pkill -P $$ 2>/dev/null || true
    sleep 1
    pkill -9 -P $$ 2>/dev/null || true
    exit 130
}
trap cleanup SIGINT SIGTERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"

# ── Defaults ──────────────────────────────────────────────────────
KAGENTI_ENV="openshell"
CLUSTER_NAME="${CLUSTER_NAME:-kagenti}"
SKIP_CREATE=false
SKIP_DESTROY=false
SKIP_TEST=false
SKIP_AGENTS=false

# ── Parse arguments ──────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-cluster-create)  SKIP_CREATE=true;  shift ;;
        --skip-cluster-destroy) SKIP_DESTROY=true; shift ;;
        --skip-test)            SKIP_TEST=true;    shift ;;
        --skip-agents)          SKIP_AGENTS=true;  shift ;;
        --cluster-name)         CLUSTER_NAME="$2"; shift 2 ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Run with no args for full test, or use --skip-* flags." >&2
            exit 1
            ;;
    esac
done

# ── Colors / logging ────────────────────────────────────────────
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

log_phase() {
    echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}┃${NC} $1"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
}
log_step()  { echo -e "${GREEN}>>>${NC} $1"; }
log_error() { echo -e "${RED}ERROR:${NC} $1" >&2; }

cd "$REPO_ROOT"

echo ""
echo "OpenShell PoC Full Test"
echo "  Cluster:   $CLUSTER_NAME"
echo "  Env:       $KAGENTI_ENV"
echo "  Phases:"
echo "    cluster-create:   $([ "$SKIP_CREATE"  = "true" ] && echo SKIP || echo RUN)"
echo "    kagenti-install:  RUN"
echo "    openshell-deploy: RUN"
echo "    agents-deploy:    $([ "$SKIP_AGENTS"  = "true" ] && echo SKIP || echo RUN)"
echo "    test:             $([ "$SKIP_TEST"    = "true" ] && echo SKIP || echo RUN)"
echo "    cluster-destroy:  $([ "$SKIP_DESTROY" = "true" ] && echo SKIP || echo RUN)"
echo ""

# ============================================================================
# PHASE 1: Create Kind Cluster
# ============================================================================
if [ "$SKIP_CREATE" = "false" ]; then
    log_phase "PHASE 1: Create Kind Cluster"
    log_step "Creating cluster: $CLUSTER_NAME"
    CLUSTER_NAME="$CLUSTER_NAME" ./.github/scripts/kind/create-cluster.sh
else
    log_phase "PHASE 1: Skipping Cluster Creation"
fi

# ============================================================================
# PHASE 2: Install Kagenti Platform (headless — no UI, no backend)
# ============================================================================
log_phase "PHASE 2: Install Kagenti Platform (OpenShell profile)"

log_step "Creating secrets..."
./.github/scripts/common/20-create-secrets.sh

log_step "Running Ansible installer (--env $KAGENTI_ENV)..."
./.github/scripts/kagenti-operator/30-run-installer.sh --env "$KAGENTI_ENV"

log_step "Waiting for platform to be ready..."
./.github/scripts/common/40-wait-platform-ready.sh

log_step "Configuring dockerhost..."
./.github/scripts/common/70-configure-dockerhost.sh

log_step "Waiting for CRDs..."
./.github/scripts/kagenti-operator/41-wait-crds.sh

# ============================================================================
# PHASE 3: Deploy OpenShell Gateway
# ============================================================================
log_phase "PHASE 3: Deploy OpenShell Gateway"

log_step "Applying OpenShell manifests (kubectl apply -k)..."
kubectl apply -k deployments/openshell/

log_step "Waiting for openshell-system namespace pods..."
kubectl wait --for=condition=ready pod --all -n openshell-system --timeout=120s 2>/dev/null || {
    log_step "No pods in openshell-system yet (gateway may be config-only). Continuing."
}

# ============================================================================
# PHASE 4: Deploy Agents
# ============================================================================
if [ "$SKIP_AGENTS" = "false" ]; then
    log_phase "PHASE 4: Deploy Agents"

    AGENTS_DIR="deployments/openshell/agents"
    if [ -d "$AGENTS_DIR" ]; then
        log_step "Applying agent manifests from $AGENTS_DIR..."
        kubectl apply -k "$AGENTS_DIR" || kubectl apply -f "$AGENTS_DIR/"
    else
        log_step "No agent manifests at $AGENTS_DIR yet (created in later tasks). Skipping."
    fi
else
    log_phase "PHASE 4: Skipping Agent Deployment"
fi

# ============================================================================
# PHASE 5: Run E2E Tests
# ============================================================================
if [ "$SKIP_TEST" = "false" ]; then
    log_phase "PHASE 5: Run E2E Tests"

    log_step "Installing test dependencies..."
    ./.github/scripts/common/80-install-test-deps.sh

    log_step "Setting up test credentials..."
    ./.github/scripts/common/87-setup-test-credentials.sh

    export KAGENTI_CONFIG_FILE="deployments/envs/dev_values_openshell.yaml"
    log_step "KAGENTI_CONFIG_FILE: $KAGENTI_CONFIG_FILE"

    TEST_DIR="kagenti/tests/e2e/openshell"
    if [ -d "$TEST_DIR" ]; then
        log_step "Running OpenShell E2E tests..."
        uv run pytest "$TEST_DIR" -v
    else
        log_step "No tests at $TEST_DIR yet (created in later tasks). Skipping."
    fi
else
    log_phase "PHASE 5: Skipping E2E Tests"
fi

# ============================================================================
# PHASE 6: Destroy Kind Cluster
# ============================================================================
if [ "$SKIP_DESTROY" = "false" ]; then
    log_phase "PHASE 6: Destroy Kind Cluster"
    CLUSTER_NAME="$CLUSTER_NAME" ./.github/scripts/kind/destroy-cluster.sh
else
    log_phase "PHASE 6: Skipping Cluster Destruction"
    echo ""
    echo "Cluster kept for debugging. To destroy later:"
    echo "  CLUSTER_NAME=$CLUSTER_NAME ./.github/scripts/kind/destroy-cluster.sh"
    echo ""
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}┃${NC} OpenShell PoC full test completed successfully!"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
