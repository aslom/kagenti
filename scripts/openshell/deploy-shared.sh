#!/usr/bin/env bash
# ============================================================================
# OPENSHELL SHARED INFRASTRUCTURE
# ============================================================================
# Deploys cluster-wide shared infrastructure for the OpenShell MVP:
#   1. agent-sandbox-controller (kubernetes-sigs, upstream image)
#   2. Gateway API experimental CRDs (TCPRoute/TLSRoute, Kind only)
#   3. cert-manager CA chain (ClusterIssuer + CA Certificate)
#   4. Keycloak realm (openshell realm, PKCE client, test users)
#
# Idempotent: safe to re-run. Checks existing state before each step.
#
# Usage:
#   scripts/openshell/deploy-shared.sh                  # Deploy everything
#   scripts/openshell/deploy-shared.sh --skip-sandbox   # Skip agent-sandbox
#   scripts/openshell/deploy-shared.sh --dry-run        # Print commands only
#   scripts/openshell/deploy-shared.sh --help           # Show usage
#
# Prerequisites: kubectl, cert-manager installed, Keycloak running
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# ── Versions (keep in sync with scripts/kind/setup-kagenti.sh) ──────────────
AGENT_SANDBOX_VERSION="v0.3.10"
GATEWAY_API_VERSION="v1.4.0"

# ── Defaults ────────────────────────────────────────────────────────────────
KEYCLOAK_NS="${KEYCLOAK_NS:-keycloak}"
KEYCLOAK_POD="keycloak-0"
KCADM="/opt/keycloak/bin/kcadm.sh"
KC_CONFIG="/tmp/kc/kcadm.config"

STEP_SANDBOX=true
STEP_GATEWAY_API=true
STEP_TLS=true
STEP_KEYCLOAK=true
DRY_RUN=false

# ── Colors & logging ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info()    { echo -e "${BLUE}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warn()    { echo -e "${YELLOW}⚠${NC} $1"; }
log_error()   { echo -e "${RED}✗${NC} $1"; }

run_cmd() {
  if $DRY_RUN; then echo "  [dry-run] $*"; else "$@"; fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Deploy OpenShell shared infrastructure (idempotent).

Options:
  --help              Show this help message
  --skip-sandbox      Skip agent-sandbox-controller installation
  --skip-gateway-api  Skip experimental Gateway API CRDs
  --skip-tls          Skip cert-manager CA chain
  --skip-keycloak     Skip Keycloak realm setup
  --keycloak-ns NS    Keycloak namespace (default: keycloak)
  --dry-run           Print commands without executing
EOF
  exit 0
}

# ── Argument parsing ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help)             usage ;;
    --skip-sandbox)     STEP_SANDBOX=false; shift ;;
    --skip-gateway-api) STEP_GATEWAY_API=false; shift ;;
    --skip-tls)         STEP_TLS=false; shift ;;
    --skip-keycloak)    STEP_KEYCLOAK=false; shift ;;
    --keycloak-ns)      KEYCLOAK_NS="$2"; shift 2 ;;
    --dry-run)          DRY_RUN=true; shift ;;
    *)
      log_error "Unknown option: $1"
      usage
      ;;
  esac
done

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  OpenShell Shared Infrastructure                             ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo "  Sandbox controller: $STEP_SANDBOX"
echo "  Gateway API CRDs:   $STEP_GATEWAY_API"
echo "  cert-manager CA:    $STEP_TLS"
echo "  Keycloak realm:     $STEP_KEYCLOAK"
echo "  Keycloak namespace: $KEYCLOAK_NS"
echo "  Dry run:            $DRY_RUN"
echo ""

# ── Helper: wait for deployment ─────────────────────────────────────────────
wait_deployment_ready() {
  local name=$1 namespace=$2 timeout=${3:-120}
  if $DRY_RUN; then return 0; fi
  log_info "Waiting for deployment $name in $namespace (timeout: ${timeout}s)..."
  kubectl wait --for=condition=Available deployment/"$name" \
    -n "$namespace" --timeout="${timeout}s"
}

# ── Helper: detect OpenShift ────────────────────────────────────────────────
is_openshift() {
  kubectl get crd routes.route.openshift.io &>/dev/null
}

# ============================================================================
# Step 1: agent-sandbox-controller
# ============================================================================
if $STEP_SANDBOX; then
  log_info "Step 1: agent-sandbox-controller (${AGENT_SANDBOX_VERSION})"

  if kubectl get crd sandboxes.agents.x-k8s.io &>/dev/null \
     && kubectl get deployment agent-sandbox-controller -n agent-sandbox-system &>/dev/null; then
    log_success "agent-sandbox already installed — skipping"
  else
    log_info "Installing agent-sandbox ${AGENT_SANDBOX_VERSION} (controller)..."
    run_cmd kubectl apply -f \
      "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/manifest.yaml"

    log_info "Installing agent-sandbox ${AGENT_SANDBOX_VERSION} (extensions)..."
    run_cmd kubectl apply -f \
      "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/extensions.yaml"

    if ! $DRY_RUN; then
      log_info "Waiting for agent-sandbox CRDs to become established..."
      kubectl wait --for=condition=Established crd \
        sandboxes.agents.x-k8s.io \
        --timeout=60s
    fi
    wait_deployment_ready agent-sandbox-controller agent-sandbox-system
    log_success "agent-sandbox installed"
  fi
  echo ""
fi

# ============================================================================
# Step 2: Gateway API experimental CRDs (Kind only)
# ============================================================================
if $STEP_GATEWAY_API; then
  log_info "Step 2: Gateway API experimental CRDs (${GATEWAY_API_VERSION})"

  if is_openshift; then
    log_info "OpenShift detected — skipping experimental Gateway API CRDs (OCP uses Routes)"
  elif kubectl get crd tcproutes.gateway.networking.k8s.io &>/dev/null; then
    log_success "Experimental Gateway API CRDs already installed — skipping"
  else
    log_info "Installing Gateway API ${GATEWAY_API_VERSION} (experimental bundle)..."
    run_cmd kubectl apply -f \
      "https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/experimental-install.yaml"

    if ! $DRY_RUN; then
      log_info "Waiting for experimental CRDs to become established..."
      kubectl wait --for=condition=Established crd \
        tcproutes.gateway.networking.k8s.io \
        tlsroutes.gateway.networking.k8s.io \
        --timeout=60s
    fi
    log_success "Experimental Gateway API CRDs installed"
  fi
  echo ""
fi

# ============================================================================
# Step 3: cert-manager CA chain
# ============================================================================
if $STEP_TLS; then
  log_info "Step 3: cert-manager CA chain for OpenShell TLS"

  # Verify cert-manager is installed
  if ! kubectl get deployment cert-manager-webhook -n cert-manager &>/dev/null; then
    log_error "cert-manager is not installed. Install cert-manager first."
    exit 1
  fi

  # 3a: Bootstrap self-signed ClusterIssuer
  log_info "Applying ClusterIssuer openshell-selfsigned..."
  run_cmd kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: openshell-selfsigned
spec:
  selfSigned: {}
EOF

  # 3b: CA certificate (self-signed, isCA)
  log_info "Applying CA Certificate openshell-ca..."
  run_cmd kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: openshell-ca
  namespace: cert-manager
spec:
  isCA: true
  commonName: openshell-ca
  secretName: openshell-ca-secret
  duration: 87600h
  renewBefore: 720h
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: openshell-selfsigned
    kind: ClusterIssuer
EOF

  # Wait for the CA certificate to be issued
  if ! $DRY_RUN; then
    log_info "Waiting for CA certificate to be ready..."
    kubectl wait --for=condition=Ready certificate/openshell-ca \
      -n cert-manager --timeout=60s
  fi

  # 3c: CA issuer (signs tenant leaf certs)
  log_info "Applying ClusterIssuer openshell-ca-issuer..."
  run_cmd kubectl apply -f - <<'EOF'
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: openshell-ca-issuer
spec:
  ca:
    secretName: openshell-ca-secret
EOF

  if ! $DRY_RUN; then
    log_info "Waiting for CA issuer to become ready..."
    kubectl wait --for=condition=Ready clusterissuer/openshell-ca-issuer \
      --timeout=60s
  fi

  log_success "cert-manager CA chain ready"
  echo ""
fi

# ============================================================================
# Step 4: Keycloak realm
# ============================================================================
if $STEP_KEYCLOAK; then
  log_info "Step 4: Keycloak realm (openshell)"

  # Verify Keycloak pod is running
  if ! kubectl get pod "$KEYCLOAK_POD" -n "$KEYCLOAK_NS" &>/dev/null; then
    log_error "Keycloak pod $KEYCLOAK_POD not found in namespace $KEYCLOAK_NS"
    exit 1
  fi

  if $DRY_RUN; then
    log_info "[dry-run] Would create openshell realm, client, users, roles, groups"
    echo ""
  else
    # Read Keycloak admin credentials
    KC_USER=$(kubectl get secret keycloak-initial-admin -n "$KEYCLOAK_NS" \
      -o jsonpath='{.data.username}' | base64 -d)
    KC_PASS=$(kubectl get secret keycloak-initial-admin -n "$KEYCLOAK_NS" \
      -o jsonpath='{.data.password}' | base64 -d)

    kc_exec() {
      kubectl exec -n "$KEYCLOAK_NS" "$KEYCLOAK_POD" -- bash -c "$1"
    }

    # Login to Keycloak
    log_info "Logging in to Keycloak as $KC_USER..."
    kc_exec "$KCADM config credentials --server http://localhost:8080 \
      --realm master --user '$KC_USER' --password '$KC_PASS' \
      --config $KC_CONFIG" >/dev/null 2>&1

    # 4a: Create realm
    log_info "Creating realm: openshell"
    kc_exec "$KCADM create realms --config $KC_CONFIG \
      -s realm=openshell -s enabled=true 2>/dev/null" 2>/dev/null || true

    # 4b: Create PKCE client
    log_info "Creating client: openshell-cli (public, PKCE)"
    kc_exec "$KCADM create clients --config $KC_CONFIG -r openshell \
      -s clientId=openshell-cli \
      -s enabled=true \
      -s publicClient=true \
      -s 'redirectUris=[\"http://localhost:*\"]' \
      -s 'webOrigins=[\"+\"]' \
      -s directAccessGrantsEnabled=true \
      -s 'attributes={\"pkce.code.challenge.method\":\"S256\"}' \
      2>/dev/null" 2>/dev/null || true

    # 4c: Create roles
    for role in openshell-admin openshell-user; do
      log_info "Creating role: $role"
      kc_exec "$KCADM create roles --config $KC_CONFIG -r openshell \
        -s name=$role 2>/dev/null" 2>/dev/null || true
    done

    # 4d: Create groups
    for group in team1 team2; do
      log_info "Creating group: /$group"
      kc_exec "$KCADM create groups --config $KC_CONFIG -r openshell \
        -s name=$group 2>/dev/null" 2>/dev/null || true
    done

    # Helper: create user, set password, assign role and group
    create_openshell_user() {
      local username=$1 password=$2 role=$3
      shift 3
      local groups=("$@")

      log_info "Creating user: $username"
      kc_exec "$KCADM create users --config $KC_CONFIG -r openshell \
        -s username=$username -s enabled=true -s emailVerified=true \
        -s email=${username}@openshell.local \
        2>/dev/null" 2>/dev/null || true

      kc_exec "$KCADM set-password --config $KC_CONFIG -r openshell \
        --username $username --new-password $password \
        2>/dev/null" 2>/dev/null || true

      kc_exec "$KCADM add-roles --config $KC_CONFIG -r openshell \
        --uusername $username --rolename $role \
        2>/dev/null" 2>/dev/null || true

      for grp in "${groups[@]}"; do
        local grp_id
        grp_id=$(kc_exec "$KCADM get groups --config $KC_CONFIG -r openshell \
          --fields id,name 2>/dev/null" | \
          python3 -c "import sys,json; gs=json.load(sys.stdin); print(next((g['id'] for g in gs if g['name']=='$grp'),''))" 2>/dev/null || echo "")
        if [[ -n "$grp_id" ]]; then
          local user_id
          user_id=$(kc_exec "$KCADM get users --config $KC_CONFIG -r openshell \
            -q username=$username --fields id 2>/dev/null" | \
            python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "")
          if [[ -n "$user_id" ]]; then
            kc_exec "$KCADM update users/$user_id/groups/$grp_id --config $KC_CONFIG \
              -r openshell -s realm=openshell -s userId=$user_id -s groupId=$grp_id \
              --no-merge 2>/dev/null" 2>/dev/null || true
          fi
        fi
      done
    }

    # 4e: Create users
    create_openshell_user "alice" "alice123" "openshell-user" "team1"
    create_openshell_user "bob"   "bob123"   "openshell-user" "team2"
    create_openshell_user "admin" "admin123" "openshell-admin" "team1" "team2"

    # 4f: Create per-tenant client scopes with audience mappers
    # Each tenant gets an optional client scope. The CLI requests the appropriate
    # scope (e.g. --scope team1-audience) to get an audience-scoped token.
    CLIENT_ID=$(kc_exec "$KCADM get clients --config $KC_CONFIG -r openshell \
      -q clientId=openshell-cli --fields id 2>/dev/null" | \
      python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])" 2>/dev/null || echo "")

    for tenant in team1 team2; do
      log_info "Creating client scope: ${tenant}-audience"
      kc_exec "$KCADM create client-scopes --config $KC_CONFIG -r openshell \
        -s name=${tenant}-audience \
        -s protocol=openid-connect \
        2>/dev/null" 2>/dev/null || true

      # Get the scope ID to add the audience mapper
      SCOPE_ID=$(kc_exec "$KCADM get client-scopes --config $KC_CONFIG -r openshell \
        --fields id,name 2>/dev/null" | \
        python3 -c "import sys,json; ss=json.load(sys.stdin); print(next((s['id'] for s in ss if s['name']=='${tenant}-audience'),''))" 2>/dev/null || echo "")

      if [[ -n "$SCOPE_ID" ]]; then
        log_info "Adding audience mapper to ${tenant}-audience scope"
        kc_exec "$KCADM create client-scopes/$SCOPE_ID/protocol-mappers/models \
          --config $KC_CONFIG -r openshell \
          -s name=audience-${tenant} \
          -s protocol=openid-connect \
          -s protocolMapper=oidc-audience-mapper \
          -s 'config={\"included.custom.audience\":\"${tenant}\",\"id.token.claim\":\"true\",\"access.token.claim\":\"true\"}' \
          2>/dev/null" 2>/dev/null || true

        # Assign as optional scope to openshell-cli client
        if [[ -n "$CLIENT_ID" ]]; then
          kc_exec "$KCADM update clients/$CLIENT_ID/optional-client-scopes/$SCOPE_ID \
            --config $KC_CONFIG -r openshell 2>/dev/null" 2>/dev/null || true
        fi
      fi
    done

    if [[ -z "$CLIENT_ID" ]]; then
      log_warn "Could not find openshell-cli client ID — audience scopes created but not linked"
    fi

    log_success "Keycloak openshell realm configured"
    echo ""
  fi
fi

# ============================================================================
# Summary
# ============================================================================
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║  OpenShell Shared Infrastructure — Complete                  ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
if ! $DRY_RUN; then
  echo "  Verify:"
  echo "    kubectl get deployment agent-sandbox-controller -n agent-sandbox-system"
  echo "    kubectl get crd sandboxes.agents.x-k8s.io"
  echo "    kubectl get clusterissuer openshell-ca-issuer"
  echo "    kubectl get secret openshell-ca-secret -n cert-manager"
  echo ""
fi
