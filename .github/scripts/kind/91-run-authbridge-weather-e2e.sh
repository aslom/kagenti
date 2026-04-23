#!/usr/bin/env bash
# AuthBridge Weather (advanced) E2E — Keycloak token exchange + MCP inbound JWT
#
# Runs the verify flow from kagenti-extensions (authbridge/demos/weather-agent):
#   deploy_and_verify_advanced.sh
#
# Prerequisites (same as platform E2E):
#   - Kind cluster with Kagenti, Keycloak, team1, webhook + AuthBridge sidecars
#   - jq, curl, python3, git
#
# Source tree:
#   - Set KAGENTI_EXTENSIONS_ROOT to a local clone (faster, offline dev, or optional
#     companion kagenti-extensions PR checkout in CI), or
#   - Leave unset: this script shallow-clones kagenti/kagenti-extensions (see refs below).
#
# Environment:
#   KAGENTI_EXTENSIONS_ROOT   Path to kagenti-extensions repo (optional)
#   KAGENTI_EXTENSIONS_GIT_URL  Clone URL (default: https://github.com/kagenti/kagenti-extensions.git)
#   KAGENTI_EXTENSIONS_GIT_REF  Branch or tag (default: v0.4.1 — pin for reproducible CI; bump when demo updates ship)
#   NAMESPACE                 K8s namespace (default: team1)
#   SKIP_DEPLOY                 If 1, only run in-cluster verify (default: 0 = full deploy)
#
# Usage (called from run-e2e-tests.sh when RUN_AUTHBRIDGE_WEATHER_E2E=1):
#   ./.github/scripts/kind/91-run-authbridge-weather-e2e.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../lib/env-detect.sh"
# shellcheck source=/dev/null
source "$SCRIPT_DIR/../lib/logging.sh"

log_step "91" "AuthBridge Weather (advanced) E2E (kagenti-extensions)"

if ! command -v jq &>/dev/null; then
    log_error "jq is required. Install jq or run from an environment with test deps."
    exit 1
fi

if ! command -v git &>/dev/null; then
    log_error "git is required to fetch kagenti-extensions when KAGENTI_EXTENSIONS_ROOT is unset."
    exit 1
fi

export NAMESPACE="${NAMESPACE:-team1}"

if ! kubectl get namespace "$NAMESPACE" &>/dev/null; then
    log_error "Namespace $NAMESPACE not found. Deploy the platform first."
    exit 1
fi

# Preflight: Keycloak admin credentials for setup_keycloak_weather_advanced.py / token exchange
if ! kubectl get secret keycloak-admin-secret -n "$NAMESPACE" &>/dev/null; then
    log_error "Secret 'keycloak-admin-secret' not found in namespace '$NAMESPACE'."
    log_error "The installer usually creates it in agent namespaces. Re-run platform deploy or create it (AuthBridge / Keycloak docs) before AuthBridge E2E."
    exit 1
fi
log_info "Preflight OK: keycloak-admin-secret present in $NAMESPACE"

EXT_ROOT="${KAGENTI_EXTENSIONS_ROOT:-}"
CLONE_DIR=""

if [[ -n "$EXT_ROOT" ]]; then
    if [[ ! -f "$EXT_ROOT/authbridge/demos/weather-agent/deploy_and_verify_advanced.sh" ]]; then
        log_error "KAGENTI_EXTENSIONS_ROOT is set but deploy_and_verify_advanced.sh not found at:"
        log_error "  $EXT_ROOT/authbridge/demos/weather-agent/"
        exit 1
    fi
    log_info "Using KAGENTI_EXTENSIONS_ROOT: $EXT_ROOT"
else
    KAGENTI_EXTENSIONS_GIT_URL="${KAGENTI_EXTENSIONS_GIT_URL:-https://github.com/kagenti/kagenti-extensions.git}"
    KAGENTI_EXTENSIONS_GIT_REF="${KAGENTI_EXTENSIONS_GIT_REF:-v0.4.1}"
    CLONE_DIR="${TMPDIR:-/tmp}/kagenti-extensions-authbridge-e2e-$$"
    log_info "Cloning kagenti-extensions (ref: $KAGENTI_EXTENSIONS_GIT_REF) to $CLONE_DIR"
    if ! git clone --depth 1 --single-branch --branch "$KAGENTI_EXTENSIONS_GIT_REF" \
        "$KAGENTI_EXTENSIONS_GIT_URL" "$CLONE_DIR" 2>/dev/null; then
        log_info "Shallow single-branch clone failed; trying full clone + checkout ($KAGENTI_EXTENSIONS_GIT_REF)"
        git clone "$KAGENTI_EXTENSIONS_GIT_URL" "$CLONE_DIR" || {
            log_error "git clone failed: $KAGENTI_EXTENSIONS_GIT_URL"
            exit 1
        }
        (cd "$CLONE_DIR" && git checkout "$KAGENTI_EXTENSIONS_GIT_REF") || {
            log_error "Could not checkout ref: $KAGENTI_EXTENSIONS_GIT_REF"
            rm -rf "$CLONE_DIR"
            exit 1
        }
    fi
    EXT_ROOT="$CLONE_DIR"
fi

DEMO_DIR="$EXT_ROOT/authbridge/demos/weather-agent"
if [[ ! -x "$DEMO_DIR/deploy_and_verify_advanced.sh" ]]; then
    chmod +x "$DEMO_DIR/deploy_and_verify_advanced.sh" 2>/dev/null || true
fi

export SKIP_DEPLOY="${SKIP_DEPLOY:-0}"

cleanup() {
    if [[ -n "$CLONE_DIR" && -d "$CLONE_DIR" ]]; then
        log_info "Removing temp clone: $CLONE_DIR"
        rm -rf "$CLONE_DIR"
    fi
}
trap cleanup EXIT

log_info "Running: $DEMO_DIR/deploy_and_verify_advanced.sh (NAMESPACE=$NAMESPACE SKIP_DEPLOY=$SKIP_DEPLOY)"
( cd "$DEMO_DIR" && ./deploy_and_verify_advanced.sh ) || {
    log_error "AuthBridge Weather (advanced) E2E failed"
    exit 1
}

log_success "AuthBridge Weather (advanced) E2E passed"
