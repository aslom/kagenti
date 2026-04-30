#!/usr/bin/env bash
# ============================================================================
# OPENSHELL PER-TENANT DEPLOYMENT
# ============================================================================
# Deploys one tenant's OpenShell gateway stack using the charts/openshell/
# Helm chart, with auto-detection of platform (Kind vs OCP).
#
# Usage:
#   scripts/openshell/deploy-tenant.sh <team>
#   scripts/openshell/deploy-tenant.sh team1
#   scripts/openshell/deploy-tenant.sh team2 --dry-run
#   scripts/openshell/deploy-tenant.sh --help
#
# Prerequisites: helm, kubectl, Keycloak running, cert-manager installed,
#                shared infra deployed (deploy-shared.sh)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Defaults ────────────────────────────────────────────────────────────────
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"
CHART_DIR="$REPO_ROOT/charts/openshell"
HELM_RELEASE_PREFIX="openshell"
KIND_DOMAIN="localtest.me"
KIND_TLS_NODEPORT=30443
IMAGE_TAG="${OPENSHELL_IMAGE_TAG:-latest}"
DRY_RUN=false
TIMEOUT=120
EXTRA_HELM_SETS=()  # Additional --set arguments

# ── Colors & logging ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
log_error()   { echo -e "${RED}✗${NC} $1"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") <team> [OPTIONS]

Deploy a tenant's OpenShell gateway stack via Helm (idempotent).

Arguments:
  team                  Tenant name (e.g., team1, team2)

Options:
  --help               Show this help message
  --dry-run            Print helm commands without executing
  --chart-dir <path>   Helm chart directory (default: $CHART_DIR)
  --keycloak-ns <ns>   Keycloak namespace (default: keycloak)
  --image-tag <tag>    Image tag for all containers (default: latest)
  --set <key=val>      Extra helm --set values (repeatable)
  --timeout <secs>     Timeout for wait operations (default: 120)
EOF
  exit 0
}

# ── Argument parsing ────────────────────────────────────────────────────────
TENANT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)          usage ;;
    --dry-run)       DRY_RUN=true; shift ;;
    --chart-dir)     CHART_DIR="$2"; shift 2 ;;
    --keycloak-ns)   KEYCLOAK_NS="$2"; shift 2 ;;
    --image-tag)     IMAGE_TAG="$2"; shift 2 ;;
    --set)           EXTRA_HELM_SETS+=("$2"); shift 2 ;;
    --timeout)       TIMEOUT="$2"; shift 2 ;;
    -*)
      log_error "Unknown option: $1"
      usage
      ;;
    *)
      if [[ -z "$TENANT" ]]; then
        TENANT="$1"; shift
      else
        log_error "Unexpected argument: $1"
        usage
      fi
      ;;
  esac
done

if [[ -z "$TENANT" ]]; then
  log_error "Tenant name is required. Usage: $(basename "$0") <team> [OPTIONS]"
  exit 1
fi

# ── Helper: detect OpenShift ────────────────────────────────────────────────
is_openshift() {
  kubectl get crd routes.route.openshift.io &>/dev/null
}

# ── Helper: get OpenShift base domain ───────────────────────────────────────
get_ocp_base_domain() {
  kubectl get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}' 2>/dev/null
}

# ── Helper: discover Keycloak issuer URL ────────────────────────────────────
get_keycloak_issuer() {
  local kc_svc kc_url
  if is_openshift; then
    kc_url=$(kubectl get route keycloak -n "$KEYCLOAK_NS" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
    if [[ -n "$kc_url" ]]; then
      echo "https://$kc_url/realms/openshell"
      return
    fi
  fi
  # Find the Keycloak service with port 8080
  kc_svc=$(kubectl get svc -n "$KEYCLOAK_NS" -l app=keycloak \
    -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.ports[0].port}{"\n"}{end}' 2>/dev/null \
    | awk '$2 == "8080" {print $1; exit}')
  if [[ -z "$kc_svc" ]]; then
    kc_svc=$(kubectl get svc -n "$KEYCLOAK_NS" -l app.kubernetes.io/name=keycloak \
      -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.ports[0].port}{"\n"}{end}' 2>/dev/null \
      | awk '$2 == "8080" {print $1; exit}')
  fi
  kc_svc="${kc_svc:-keycloak-service}"
  echo "http://${kc_svc}.${KEYCLOAK_NS}.svc.cluster.local:8080/realms/openshell"
}

# ── Helper: generate ingress hostname ───────────────────────────────────────
get_ingress_host() {
  if is_openshift; then
    local base_domain
    base_domain=$(get_ocp_base_domain)
    echo "openshell-${TENANT}.${base_domain}"
  else
    echo "openshell-${TENANT}.${KIND_DOMAIN}"
  fi
}

# ── Helper: determine ingress type ──────────────────────────────────────────
get_ingress_type() {
  if is_openshift; then
    echo "route"
  else
    echo "istio"
  fi
}

# ============================================================================
# Main
# ============================================================================
RELEASE_NAME="${HELM_RELEASE_PREFIX}-${TENANT}"
INGRESS_TYPE=$(get_ingress_type)
INGRESS_HOST=$(get_ingress_host)
OIDC_ISSUER=$(get_keycloak_issuer)

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  OpenShell Tenant Deployment                                 ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Tenant:         $TENANT"
echo "  Release:        $RELEASE_NAME"
echo "  Namespace:      $TENANT"
echo "  Ingress type:   $INGRESS_TYPE"
echo "  Ingress host:   $INGRESS_HOST"
echo "  OIDC issuer:    $OIDC_ISSUER"
echo "  Image tag:      $IMAGE_TAG"
echo "  Chart:          $CHART_DIR"
echo "  Dry run:        $DRY_RUN"
echo ""

# ── Step 1: Create namespace with labels ────────────────────────────────────
log_info "Step 1: Namespace $TENANT"

if kubectl get namespace "$TENANT" &>/dev/null; then
  log_success "Namespace $TENANT already exists"
else
  log_info "Creating namespace $TENANT..."
  if ! $DRY_RUN; then
    kubectl create namespace "$TENANT"
  else
    echo "  [dry-run] kubectl create namespace $TENANT"
  fi
fi

if ! $DRY_RUN; then
  kubectl label namespace "$TENANT" \
    shared-gateway-access=true \
    openshell.ai/tenant="$TENANT" \
    --overwrite
fi
echo ""

# ── Step 2: Helm install/upgrade ────────────────────────────────────────────
log_info "Step 2: Helm install/upgrade $RELEASE_NAME"

HELM_ARGS=(
  upgrade "$RELEASE_NAME" "$CHART_DIR"
  --install
  --namespace "$TENANT"
  --set "tenant=$TENANT"
  --set "oidc.issuer=$OIDC_ISSUER"
  --set "oidc.audience=$TENANT"
  --set "driver.namespace=$TENANT"
  --set "ingress.type=$INGRESS_TYPE"
  --set "ingress.host=$INGRESS_HOST"
  --set "images.gateway.tag=$IMAGE_TAG"
  --set "images.computeDriver.tag=$IMAGE_TAG"
  --set "images.credentialsDriver.tag=$IMAGE_TAG"
  --wait
  --timeout "${TIMEOUT}s"
)

for extra in "${EXTRA_HELM_SETS[@]}"; do
  HELM_ARGS+=(--set "$extra")
done

if $DRY_RUN; then
  echo "  [dry-run] helm ${HELM_ARGS[*]}"
else
  helm "${HELM_ARGS[@]}"
fi
echo ""

# ── Step 3: Wait for certificates ──────────────────────────────────────────
log_info "Step 3: Waiting for cert-manager certificates"

if $DRY_RUN; then
  echo "  [dry-run] kubectl wait --for=condition=Ready certificate -n $TENANT --all --timeout=${TIMEOUT}s"
else
  if kubectl get certificate -n "$TENANT" --no-headers 2>/dev/null | grep -q .; then
    kubectl wait --for=condition=Ready certificate --all \
      -n "$TENANT" --timeout="${TIMEOUT}s"
    log_success "All certificates ready"
  else
    log_warn "No certificates found in namespace $TENANT (may be handled by Helm --wait)"
  fi
fi
echo ""

# ── Step 4: Wait for gateway pod ────────────────────────────────────────────
log_info "Step 4: Waiting for gateway pod rollout"

if $DRY_RUN; then
  echo "  [dry-run] kubectl rollout status statefulset/openshell-server -n $TENANT --timeout=${TIMEOUT}s"
else
  kubectl rollout status statefulset/openshell-server \
    -n "$TENANT" --timeout="${TIMEOUT}s"
  log_success "Gateway pod ready"
fi
echo ""

# ── Summary ─────────────────────────────────────────────────────────────────
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Tenant $TENANT — Deployment Complete                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
if ! $DRY_RUN; then
  echo "  Verify:"
  echo "    kubectl get pods -n $TENANT"
  echo "    kubectl get certificate -n $TENANT"
  if [[ "$INGRESS_TYPE" == "istio" ]]; then
    echo "    kubectl get tlsroute -n $TENANT"
  else
    echo "    kubectl get route -n $TENANT"
  fi
  echo ""
  echo "  Connect:"
  if [[ "$INGRESS_TYPE" == "istio" ]]; then
    echo "    openshell gateway set --url https://${INGRESS_HOST}:${KIND_TLS_NODEPORT}"
  else
    echo "    openshell gateway set --url https://${INGRESS_HOST}"
  fi
  echo "    openshell login"
  echo ""
fi
