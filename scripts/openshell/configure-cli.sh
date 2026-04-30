#!/usr/bin/env bash
# ============================================================================
# OPENSHELL CLI CONFIGURATION
# ============================================================================
# Extracts cert-manager client certs from K8s secrets and configures the
# local openshell CLI for mTLS gateway access. Dev-only — not needed in CI.
#
# Usage:
#   scripts/openshell/configure-cli.sh <team>
#   scripts/openshell/configure-cli.sh team1
#   scripts/openshell/configure-cli.sh team1 --dry-run
#   scripts/openshell/configure-cli.sh --help
#
# Prerequisites: kubectl, cert-manager installed, deploy-shared.sh and deploy-tenant.sh run
#
# Note: The gateway server cert must include the external hostname as a SAN
# for TLS hostname validation to succeed. See charts/openshell/ certificate
# template — add openshell-${TENANT}.${DOMAIN} to dnsNames if missing.
# ============================================================================

set -euo pipefail

# ── Defaults ────────────────────────────────────────────────────────────────
KIND_DOMAIN="localtest.me"
GATEWAY_PORT=9443
CONFIG_DIR="${OPENSHELL_CONFIG_DIR:-$HOME/.config/openshell}"
DRY_RUN=false
TENANT=""

# ── Colors & logging ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
log_error()   { echo -e "${RED}✗${NC} $1"; }

usage() {
  cat <<EOF
Usage: $(basename "$0") <team> [OPTIONS]

Extract cert-manager client certs from K8s secrets and configure the
local openshell CLI for mTLS gateway access. Dev-only, not needed in CI.

Arguments:
  team                  Tenant name (e.g., team1, team2)

Options:
  --help               Show this help message
  --config-dir <dir>   openshell config directory (default: ~/.config/openshell)
  --dry-run            Print actions without writing files
                       (note: platform detection still requires a live cluster context)

After running this script:
  openshell status     # verify gateway connection
EOF
  exit 0
}

# ── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)        usage ;;
    --dry-run)     DRY_RUN=true; shift ;;
    --config-dir)  CONFIG_DIR="$2"; shift 2 ;;
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
  log_error "Tenant name required. Usage: $(basename "$0") <team>"
  exit 1
fi

# ── Platform detection ───────────────────────────────────────────────────────
is_openshift() {
  kubectl get crd routes.route.openshift.io &>/dev/null
}

get_ocp_base_domain() {
  kubectl get ingresses.config.openshift.io cluster \
    -o jsonpath='{.spec.domain}' 2>/dev/null
}

# ── Derived values ───────────────────────────────────────────────────────────
CONTEXT="openshell-${TENANT}"
SECRET_NAME="openshell-client-tls"
SECRET_NS="$TENANT"
GATEWAY_DIR="$CONFIG_DIR/gateways/$CONTEXT"
MTLS_DIR="$GATEWAY_DIR/mtls"
ACTIVE_FILE="$CONFIG_DIR/active_gateway"
METADATA_FILE="$GATEWAY_DIR/metadata.json"

if is_openshift; then
  BASE_DOMAIN=$(get_ocp_base_domain)
  if [[ -z "$BASE_DOMAIN" ]]; then
    log_error "Could not detect OCP base domain (kubectl get ingresses.config.openshift.io cluster)"
    exit 1
  fi
  GATEWAY_ENDPOINT="https://openshell-${TENANT}.${BASE_DOMAIN}"
  IS_REMOTE="true"
  PORT=443
else
  GATEWAY_ENDPOINT="https://openshell-${TENANT}.${KIND_DOMAIN}:${GATEWAY_PORT}"
  IS_REMOTE="false"
  PORT=$GATEWAY_PORT
fi

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  OpenShell CLI Configuration                                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Tenant:          $TENANT"
echo "  Context:         $CONTEXT"
echo "  Gateway URL:     $GATEWAY_ENDPOINT"
echo "  Secret:          $SECRET_NAME (namespace: $SECRET_NS)"
echo "  Config dir:      $GATEWAY_DIR"
echo "  Dry run:         $DRY_RUN"
echo ""

# ── Step 1: Verify secret exists with all required keys ──────────────────────
log_info "Step 1: Checking secret $SECRET_NAME in namespace $SECRET_NS"

if $DRY_RUN; then
  log_warn "dry-run: skipping secret validation"
else
  if ! kubectl get secret "$SECRET_NAME" -n "$SECRET_NS" &>/dev/null; then
    log_error "Secret $SECRET_NAME not found in namespace $SECRET_NS"
    log_error "Run 'scripts/openshell/deploy-tenant.sh $TENANT' first and wait for cert-manager."
    exit 1
  fi

  for key in ca.crt tls.crt tls.key; do
    val=$(kubectl get secret "$SECRET_NAME" -n "$SECRET_NS" \
      --template="{{index .data \"$key\"}}" 2>/dev/null || true)
    if [[ -z "$val" ]]; then
      log_error "Key '$key' not found in secret $SECRET_NAME"
      log_error "The certificate may still be issuing — wait and retry."
      exit 1
    fi
  done

  log_success "Secret $SECRET_NAME found with all required keys"
fi
echo ""

# ── Step 2: Create config directories ────────────────────────────────────────
log_info "Step 2: Creating config directories"

if ! $DRY_RUN; then
  mkdir -p "$MTLS_DIR"
  chmod 700 "$MTLS_DIR"
else
  echo "  [dry-run] mkdir -p $MTLS_DIR && chmod 700"
fi

log_success "Directories ready: $GATEWAY_DIR"
echo ""

# ── Step 3: Extract certs from K8s secret ────────────────────────────────────
log_info "Step 3: Extracting certificates from secret"

extract_cert() {
  local key="$1"
  local dest="$2"
  if ! $DRY_RUN; then
    kubectl get secret "$SECRET_NAME" -n "$SECRET_NS" \
      --template="{{index .data \"$key\"}}" \
      | base64 --decode > "$dest"
    chmod 600 "$dest"
  else
    echo "  [dry-run] extract $key → $dest"
  fi
}

extract_cert "ca.crt"  "$MTLS_DIR/ca.crt"
extract_cert "tls.crt" "$MTLS_DIR/tls.crt"
extract_cert "tls.key" "$MTLS_DIR/tls.key"

log_success "Certs written to $MTLS_DIR"
echo ""

# ── Step 4: Write metadata.json ───────────────────────────────────────────────
log_info "Step 4: Writing metadata.json"

if ! $DRY_RUN; then
  cat > "$METADATA_FILE" <<EOF
{
  "name": "$CONTEXT",
  "gateway_endpoint": "$GATEWAY_ENDPOINT",
  "is_remote": $IS_REMOTE,
  "gateway_port": $PORT,
  "auth_mode": "mtls"
}
EOF
  log_success "Wrote $METADATA_FILE"
else
  echo "  [dry-run] write $METADATA_FILE:"
  cat <<EOF
  {
    "name": "$CONTEXT",
    "gateway_endpoint": "$GATEWAY_ENDPOINT",
    "is_remote": $IS_REMOTE,
    "gateway_port": $PORT,
    "auth_mode": "mtls"
  }
EOF
fi
echo ""

# ── Step 5: Set as active gateway ─────────────────────────────────────────────
log_info "Step 5: Setting $CONTEXT as active gateway"

if ! $DRY_RUN; then
  mkdir -p "$CONFIG_DIR"
  echo "$CONTEXT" > "$ACTIVE_FILE"
  log_success "Active gateway: $CONTEXT (written to $ACTIVE_FILE)"
else
  echo "  [dry-run] echo $CONTEXT > $ACTIVE_FILE"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  Done — CLI configured for tenant: $TENANT"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Verify:"
echo "    openshell status"
echo ""
