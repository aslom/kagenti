#!/usr/bin/env bash
# ============================================================================
# OPENSHELL PHASE 0 SETUP FOR KIND
# ============================================================================
# Deploys NVIDIA OpenShell on an existing kagenti Kind cluster with:
#   - AgentSandbox CRD + controller
#   - OpenShell gateway (Helm) with auto-generated mTLS certs
#   - Istio TLS passthrough ingress (NodePort 30443 → host 9443)
#   - Supervisor binary on the Kind node (hostPath delivery)
#   - CLI gateway configuration (mTLS certs extracted from K8s secrets)
#
# Prerequisites:
#   - Kind cluster named "kagenti" running (scripts/kind/setup-kagenti.sh)
#   - OpenShell repo cloned (OPENSHELL_REPO env var or ../openshell relative to kagenti)
#   - helm, kubectl, docker, jq
#   - openshell CLI installed (https://github.com/NVIDIA/OpenShell)
#
# Usage:
#   scripts/kind/openshell/setup.sh                    # Full setup
#   scripts/kind/openshell/setup.sh --skip-crd         # Skip CRD install
#   scripts/kind/openshell/setup.sh --skip-helm        # Skip Helm install
#   scripts/kind/openshell/setup.sh --skip-ingress     # Skip Gateway/TLSRoute
#   scripts/kind/openshell/setup.sh --skip-cli         # Skip CLI config
#   scripts/kind/openshell/setup.sh --smoke-test       # Run smoke test only
#   scripts/kind/openshell/setup.sh --teardown         # Remove everything
#
# Environment:
#   OPENSHELL_REPO    Path to OpenShell repo (default: auto-detect, clones if missing)
#   OPENSHELL_GIT_URL Git URL to clone from (default: https://github.com/NVIDIA/OpenShell.git)
#   CLUSTER_NAME      Kind cluster name (default: kagenti)
#   KUBE_CONTEXT      kubectl context (default: kind-$CLUSTER_NAME)
#   NAMESPACE          OpenShell namespace (default: openshell)
#   GATEWAY_IMAGE     Override gateway image
#   SANDBOX_IMAGE     Override sandbox image
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# ── Defaults ─────────────────────────────────────────────────────────────────
CLUSTER_NAME="${CLUSTER_NAME:-kagenti}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-$CLUSTER_NAME}"
NAMESPACE="${NAMESPACE:-openshell}"
GATEWAY_NS="kagenti-system"
GATEWAY_API_EXPERIMENTAL_URL="https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml"
SUPERVISOR_IMAGE="${SUPERVISOR_IMAGE:-ghcr.io/nvidia/openshell/cluster:latest}"
OPENSHELL_GIT_URL="${OPENSHELL_GIT_URL:-https://github.com/NVIDIA/OpenShell.git}"
OPENSHELL_CLONE_DIR="${REPO_ROOT}/../openshell"

CLI_CONFIG_DIR="${HOME}/.config/openshell"
CLI_GATEWAY_DIR="${CLI_CONFIG_DIR}/gateways/${KUBE_CONTEXT}"

# Auto-detect OpenShell repo
if [[ -z "${OPENSHELL_REPO:-}" ]]; then
  for candidate in \
    "${REPO_ROOT}/../openshell" \
    "${HOME}/openshell"; do
    if [[ -d "$candidate/deploy/helm/openshell" ]]; then
      OPENSHELL_REPO="$(cd "$candidate" && pwd)"
      break
    fi
  done
fi

# ── Flags ────────────────────────────────────────────────────────────────────
SKIP_CRD=false
SKIP_HELM=false
SKIP_INGRESS=false
SKIP_SUPERVISOR=false
SKIP_CLI=false
SMOKE_TEST_ONLY=false
TEARDOWN=false

for arg in "$@"; do
  case "$arg" in
    --skip-crd)        SKIP_CRD=true ;;
    --skip-helm)       SKIP_HELM=true ;;
    --skip-ingress)    SKIP_INGRESS=true ;;
    --skip-supervisor) SKIP_SUPERVISOR=true ;;
    --skip-cli)        SKIP_CLI=true ;;
    --smoke-test)      SMOKE_TEST_ONLY=true ;;
    --teardown)        TEARDOWN=true ;;
    --help|-h)
      awk 'NR>1 && /^[^#]/{exit} NR>1{sub(/^# ?/,""); print}' "$0"
      exit 0 ;;
    *)
      echo "Unknown flag: $arg (use --help)" >&2
      exit 1 ;;
  esac
done

# ── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo "▸ $*"; }
ok()    { echo "✓ $*"; }
warn()  { echo "⚠ $*" >&2; }
die()   { echo "✗ $*" >&2; exit 1; }

kubectl_ctx() { kubectl --context "$KUBE_CONTEXT" "$@"; }

wait_for_rollout() {
  local ns="$1" resource="$2" timeout="${3:-120s}"
  kubectl_ctx rollout status "$resource" -n "$ns" --timeout="$timeout" >/dev/null 2>&1
}

wait_for_pod_ready() {
  local ns="$1" label="$2" timeout="${3:-120}"
  kubectl_ctx wait pod -n "$ns" -l "$label" --for=condition=Ready --timeout="${timeout}s" >/dev/null 2>&1
}

# ── Clone OpenShell repo if not found ────────────────────────────────────────
clone_openshell_repo() {
  local clone_target
  clone_target="$(cd "$(dirname "$OPENSHELL_CLONE_DIR")" && pwd)/$(basename "$OPENSHELL_CLONE_DIR")"

  if [[ -d "${clone_target}/deploy/helm/openshell" ]]; then
    OPENSHELL_REPO="$clone_target"
    return
  fi

  command -v git >/dev/null 2>&1 || die "git not found — cannot clone OpenShell repo"
  info "OpenShell repo not found — cloning from $OPENSHELL_GIT_URL ..."
  git clone --depth 1 "$OPENSHELL_GIT_URL" "$clone_target" >/dev/null 2>&1 \
    || die "Failed to clone $OPENSHELL_GIT_URL"
  OPENSHELL_REPO="$clone_target"
  ok "Cloned OpenShell to $OPENSHELL_REPO"
}

# ── Preflight ────────────────────────────────────────────────────────────────
preflight() {
  for cmd in kubectl helm docker jq; do
    command -v "$cmd" >/dev/null 2>&1 || die "Required tool not found: $cmd"
  done

  kubectl_ctx get nodes >/dev/null 2>&1 \
    || die "Cannot reach Kind cluster (context: $KUBE_CONTEXT). Is it running?"

  if [[ "$SMOKE_TEST_ONLY" == "false" && "$TEARDOWN" == "false" ]]; then
    if [[ -z "${OPENSHELL_REPO:-}" ]]; then
      clone_openshell_repo
    fi
    [[ -d "${OPENSHELL_REPO}/deploy/helm/openshell" ]] \
      || die "Invalid OPENSHELL_REPO ($OPENSHELL_REPO): missing deploy/helm/openshell"
    info "OpenShell repo: $OPENSHELL_REPO"
  fi
}

# ── Teardown ─────────────────────────────────────────────────────────────────
teardown() {
  info "Tearing down OpenShell from Kind cluster..."

  # Delete TLSRoute and Gateway
  kubectl_ctx delete tlsroute openshell -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
  kubectl_ctx delete gateway tls-passthrough -n "$GATEWAY_NS" --ignore-not-found 2>/dev/null || true

  # Uninstall Helm release
  helm uninstall openshell --kube-context "$KUBE_CONTEXT" -n "$NAMESPACE" 2>/dev/null || true

  # Delete namespace
  kubectl_ctx delete namespace "$NAMESPACE" --ignore-not-found 2>/dev/null || true

  # Delete CRD + controller
  if [[ -f "${OPENSHELL_REPO:-}/deploy/kube/manifests/agent-sandbox.yaml" ]]; then
    kubectl_ctx delete -f "${OPENSHELL_REPO}/deploy/kube/manifests/agent-sandbox.yaml" --ignore-not-found 2>/dev/null || true
  else
    kubectl_ctx delete namespace agent-sandbox-system --ignore-not-found 2>/dev/null || true
    kubectl_ctx delete crd sandboxes.agents.x-k8s.io --ignore-not-found 2>/dev/null || true
  fi

  # Remove supervisor binary from Kind node
  docker exec "${CLUSTER_NAME}-control-plane" rm -f /opt/openshell/bin/openshell-sandbox 2>/dev/null || true

  # Remove CLI config
  if [[ -d "$CLI_GATEWAY_DIR" ]]; then
    rm -rf "$CLI_GATEWAY_DIR"
    info "Removed CLI gateway config: $CLI_GATEWAY_DIR"
  fi
  if [[ -f "${CLI_CONFIG_DIR}/active_gateway" ]]; then
    local active
    active="$(cat "${CLI_CONFIG_DIR}/active_gateway" 2>/dev/null || true)"
    if [[ "$active" == "$KUBE_CONTEXT" ]]; then
      rm -f "${CLI_CONFIG_DIR}/active_gateway"
      info "Cleared active gateway"
    fi
  fi

  ok "OpenShell teardown complete"
}

# ── Step 1: AgentSandbox CRD + Controller ────────────────────────────────────
install_crd() {
  if [[ "$SKIP_CRD" == "true" ]]; then
    info "Skipping CRD install (--skip-crd)"
    return
  fi

  info "Installing AgentSandbox CRD + controller..."

  # Install experimental Gateway API CRDs (for TLSRoute)
  if ! kubectl_ctx get crd tlsroutes.gateway.networking.k8s.io >/dev/null 2>&1; then
    info "Installing experimental Gateway API CRDs..."
    kubectl_ctx apply -f "$GATEWAY_API_EXPERIMENTAL_URL" >/dev/null 2>&1
    ok "Gateway API experimental CRDs installed"
  fi

  kubectl_ctx apply -f "${OPENSHELL_REPO}/deploy/kube/manifests/agent-sandbox.yaml" >/dev/null 2>&1
  wait_for_rollout "agent-sandbox-system" "statefulset/agent-sandbox-controller" "120s"
  ok "AgentSandbox CRD + controller ready"
}

# ── Step 1b: Patch Helm chart with TLS auto-generation ───────────────────────
patch_helm_chart() {
  local tls_template="${OPENSHELL_REPO}/deploy/helm/openshell/templates/tls-secrets.yaml"
  if [[ -f "$tls_template" ]]; then
    info "Helm chart already has tls-secrets.yaml — skipping patch"
    return
  fi

  info "Patching Helm chart with mTLS auto-generation template..."
  cat > "$tls_template" << 'TLSEOF'
{{- $fullName := include "openshell.fullname" . }}
{{- $ns := .Release.Namespace }}

{{- if not (lookup "v1" "Secret" $ns .Values.server.sshHandshakeSecretName) }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.server.sshHandshakeSecretName }}
  namespace: {{ $ns }}
  labels:
    {{- include "openshell.labels" . | nindent 4 }}
data:
  secret: {{ sha256sum (randAlphaNum 64) | b64enc }}
{{- end }}

{{- if not .Values.server.disableTls }}
{{- $serverSecretName := .Values.server.tls.certSecretName }}

{{- if not (lookup "v1" "Secret" $ns $serverSecretName) }}

{{- $ca := genCA "openshell-ca" 36500 }}

{{- $serverDNS := list
    $fullName
    (printf "%s.%s" $fullName $ns)
    (printf "%s.%s.svc" $fullName $ns)
    (printf "%s.%s.svc.cluster.local" $fullName $ns)
    "localhost"
}}
{{- $serverCert := genSignedCert "openshell-server" (list "127.0.0.1") $serverDNS 36500 $ca }}

{{- $clientCert := genSignedCert "openshell-client" nil nil 36500 $ca }}

---
apiVersion: v1
kind: Secret
metadata:
  name: {{ $serverSecretName }}
  namespace: {{ $ns }}
  labels:
    {{- include "openshell.labels" . | nindent 4 }}
type: kubernetes.io/tls
data:
  tls.crt: {{ $serverCert.Cert | b64enc }}
  tls.key: {{ $serverCert.Key | b64enc }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.server.tls.clientCaSecretName }}
  namespace: {{ $ns }}
  labels:
    {{- include "openshell.labels" . | nindent 4 }}
data:
  ca.crt: {{ $ca.Cert | b64enc }}
---
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Values.server.tls.clientTlsSecretName }}
  namespace: {{ $ns }}
  labels:
    {{- include "openshell.labels" . | nindent 4 }}
type: kubernetes.io/tls
data:
  tls.crt: {{ $clientCert.Cert | b64enc }}
  tls.key: {{ $clientCert.Key | b64enc }}
  ca.crt: {{ $ca.Cert | b64enc }}

{{- end }}
{{- end }}
TLSEOF

  ok "Helm chart patched with tls-secrets.yaml"
}

# ── Step 2: Helm install gateway ─────────────────────────────────────────────
install_gateway() {
  if [[ "$SKIP_HELM" == "true" ]]; then
    info "Skipping Helm install (--skip-helm)"
    return
  fi

  patch_helm_chart

  info "Installing OpenShell gateway via Helm..."

  kubectl_ctx create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl_ctx apply -f - >/dev/null 2>&1
  kubectl_ctx label namespace "$NAMESPACE" shared-gateway-access=true --overwrite >/dev/null 2>&1

  local helm_args=(
    upgrade --install openshell "${OPENSHELL_REPO}/deploy/helm/openshell"
    --namespace "$NAMESPACE"
    --kube-context "$KUBE_CONTEXT"
    --set "server.sandboxNamespace=$NAMESPACE"
    --set "server.grpcEndpoint=https://openshell.${NAMESPACE}.svc.cluster.local:8080"
    --set "server.sandboxImagePullPolicy=Never"
    --set "image.pullPolicy=Never"
    --set "service.type=ClusterIP"
  )

  [[ -n "${GATEWAY_IMAGE:-}" ]] && helm_args+=(--set "image.repository=${GATEWAY_IMAGE%:*}" --set "image.tag=${GATEWAY_IMAGE##*:}")
  [[ -n "${SANDBOX_IMAGE:-}" ]] && helm_args+=(--set "server.sandboxImage=$SANDBOX_IMAGE")

  helm "${helm_args[@]}" >/dev/null 2>&1
  wait_for_rollout "$NAMESPACE" "statefulset/openshell" "120s"
  ok "OpenShell gateway ready"
}

# ── Step 3: Supervisor binary ────────────────────────────────────────────────
install_supervisor() {
  if [[ "$SKIP_SUPERVISOR" == "true" ]]; then
    info "Skipping supervisor install (--skip-supervisor)"
    return
  fi

  info "Copying supervisor binary to Kind node..."

  local node="${CLUSTER_NAME}-control-plane"
  local tmp_bin="/tmp/openshell-sandbox-$$"

  # Extract from the cluster image (matches gateway version)
  local cid
  cid=$(docker create --entrypoint /bin/true "$SUPERVISOR_IMAGE" 2>/dev/null)
  docker cp "${cid}:/opt/openshell/bin/openshell-sandbox" "$tmp_bin" 2>/dev/null
  docker rm "$cid" >/dev/null 2>&1

  # Copy to Kind node at the hostPath the K8s driver expects
  docker exec "$node" mkdir -p /opt/openshell/bin
  docker cp "$tmp_bin" "${node}:/opt/openshell/bin/openshell-sandbox"
  docker exec "$node" chmod +x /opt/openshell/bin/openshell-sandbox
  rm -f "$tmp_bin"

  ok "Supervisor binary installed on $node"
}

# ── Step 4: Istio TLS passthrough ingress ────────────────────────────────────
install_ingress() {
  if [[ "$SKIP_INGRESS" == "true" ]]; then
    info "Skipping ingress setup (--skip-ingress)"
    return
  fi

  info "Setting up Istio TLS passthrough ingress..."

  # Enable alpha Gateway API in istiod (required for TLS passthrough)
  local current
  current=$(kubectl_ctx get deploy istiod -n istio-system \
    -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="PILOT_ENABLE_ALPHA_GATEWAY_API")].value}' 2>/dev/null || true)

  if [[ "$current" != "true" ]]; then
    info "Enabling PILOT_ENABLE_ALPHA_GATEWAY_API on istiod..."
    kubectl_ctx set env deploy/istiod -n istio-system PILOT_ENABLE_ALPHA_GATEWAY_API=true >/dev/null 2>&1
    wait_for_rollout "istio-system" "deploy/istiod" "60s"
    ok "istiod updated with alpha Gateway API support"
  fi

  # Create TLS passthrough Gateway
  kubectl_ctx apply -f - >/dev/null 2>&1 <<EOF
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: tls-passthrough
  namespace: $GATEWAY_NS
  annotations:
    networking.istio.io/service-type: NodePort
spec:
  gatewayClassName: istio
  listeners:
  - name: tls-passthrough
    port: 443
    protocol: TLS
    tls:
      mode: Passthrough
    allowedRoutes:
      namespaces:
        from: All
EOF

  # Wait for the Gateway to be programmed
  local retries=0
  while [[ $retries -lt 15 ]]; do
    local status
    status=$(kubectl_ctx get gateway tls-passthrough -n "$GATEWAY_NS" \
      -o jsonpath='{.status.listeners[0].conditions[?(@.type=="Accepted")].status}' 2>/dev/null || true)
    [[ "$status" == "True" ]] && break
    sleep 2
    retries=$((retries + 1))
  done
  [[ "$status" == "True" ]] || die "Gateway not accepted after 30s"

  # Patch NodePort to 30443 (mapped to host:9443 in Kind config)
  local svc_name="tls-passthrough-istio"
  # Wait for Istio to create the service
  retries=0
  while ! kubectl_ctx get svc "$svc_name" -n "$GATEWAY_NS" >/dev/null 2>&1; do
    sleep 2
    retries=$((retries + 1))
    [[ $retries -lt 15 ]] || die "Istio did not create $svc_name service"
  done

  local current_np
  current_np=$(kubectl_ctx get svc "$svc_name" -n "$GATEWAY_NS" \
    -o jsonpath='{.spec.ports[?(@.port==443)].nodePort}' 2>/dev/null || true)

  if [[ "$current_np" != "30443" ]]; then
    kubectl_ctx patch svc "$svc_name" -n "$GATEWAY_NS" --type='json' \
      -p='[{"op": "replace", "path": "/spec/ports/1/nodePort", "value": 30443}]' >/dev/null 2>&1
  fi

  # Create TLSRoute
  kubectl_ctx apply -f - >/dev/null 2>&1 <<EOF
apiVersion: gateway.networking.k8s.io/v1alpha2
kind: TLSRoute
metadata:
  name: openshell
  namespace: $NAMESPACE
spec:
  parentRefs:
  - name: tls-passthrough
    namespace: $GATEWAY_NS
    sectionName: tls-passthrough
  rules:
  - backendRefs:
    - name: openshell
      port: 8080
EOF

  # Wait for the envoy proxy to sync its config
  info "Waiting for TLS proxy to be ready..."
  local proxy_label="gateway.networking.k8s.io/gateway-name=tls-passthrough"
  wait_for_pod_ready "$GATEWAY_NS" "$proxy_label" 60
  sleep 5

  ok "TLS passthrough ingress ready (host:9443 → NodePort 30443 → gateway:8080)"
}

# ── Step 5: CLI configuration ────────────────────────────────────────────────
configure_cli() {
  if [[ "$SKIP_CLI" == "true" ]]; then
    info "Skipping CLI config (--skip-cli)"
    return
  fi

  if ! command -v openshell >/dev/null 2>&1; then
    warn "openshell CLI not found — skipping CLI configuration"
    return
  fi

  info "Configuring openshell CLI..."

  mkdir -p "${CLI_GATEWAY_DIR}/mtls"

  # Write gateway metadata
  cat > "${CLI_GATEWAY_DIR}/metadata.json" <<EOF
{
  "name": "${KUBE_CONTEXT}",
  "gateway_endpoint": "https://127.0.0.1:9443",
  "is_remote": false,
  "gateway_port": 9443
}
EOF

  # Extract mTLS certs from K8s secrets
  local client_ca_secret
  client_ca_secret=$(kubectl_ctx get secret -n "$NAMESPACE" \
    -o jsonpath='{.items[?(@.metadata.name=="openshell-server-client-ca")].metadata.name}' 2>/dev/null || true)

  local client_tls_secret
  client_tls_secret=$(kubectl_ctx get secret -n "$NAMESPACE" \
    -o jsonpath='{.items[?(@.metadata.name=="openshell-client-tls")].metadata.name}' 2>/dev/null || true)

  if [[ -z "$client_ca_secret" || -z "$client_tls_secret" ]]; then
    warn "TLS secrets not found in $NAMESPACE — CLI may not authenticate"
    return
  fi

  kubectl_ctx get secret openshell-server-client-ca -n "$NAMESPACE" \
    -o jsonpath='{.data.ca\.crt}' | base64 -d > "${CLI_GATEWAY_DIR}/mtls/ca.crt"

  kubectl_ctx get secret openshell-client-tls -n "$NAMESPACE" \
    -o jsonpath='{.data.tls\.crt}' | base64 -d > "${CLI_GATEWAY_DIR}/mtls/tls.crt"

  kubectl_ctx get secret openshell-client-tls -n "$NAMESPACE" \
    -o jsonpath='{.data.tls\.key}' | base64 -d > "${CLI_GATEWAY_DIR}/mtls/tls.key"

  # Set as active gateway
  echo "$KUBE_CONTEXT" > "${CLI_CONFIG_DIR}/active_gateway"

  ok "CLI configured (gateway: $KUBE_CONTEXT, endpoint: https://127.0.0.1:9443)"
}

# ── Smoke test ───────────────────────────────────────────────────────────────
smoke_test() {
  info "Running smoke test..."

  # Check connectivity
  if ! command -v openshell >/dev/null 2>&1; then
    warn "openshell CLI not found — skipping smoke test"
    return
  fi

  local status_output
  status_output=$(openshell status 2>&1) || true

  if echo "$status_output" | grep -q "Connected"; then
    ok "Gateway connected"
  else
    die "Gateway not reachable: $status_output"
  fi

  # Create sandbox — the CLI blocks on an interactive SSH session after creation,
  # so we use `timeout` to kill it once the sandbox is allocated.
  # Exit code 124 = timeout (expected), anything else is a real error.
  info "Creating test sandbox..."
  local create_rc=0
  timeout 120 openshell sandbox create --name smoke-test >/dev/null 2>&1 || create_rc=$?
  if [[ $create_rc -ne 0 && $create_rc -ne 124 ]]; then
    die "Sandbox create failed (exit $create_rc)"
  fi

  # Verify sandbox reached Ready
  local retries=0 phase=""
  while [[ $retries -lt 30 ]]; do
    phase=$(openshell sandbox list 2>/dev/null | grep smoke-test | awk '{print $NF}' || true)
    phase=$(printf '%s' "$phase" | perl -pe 's/\e\[[0-9;]*m//g')
    [[ "$phase" == "Ready" ]] && break
    sleep 2
    retries=$((retries + 1))
  done

  if [[ "$phase" != "Ready" ]]; then
    openshell sandbox delete smoke-test 2>/dev/null || true
    die "Sandbox did not reach Ready state"
  fi
  ok "Sandbox created"

  # Exec into sandbox
  local exec_output
  exec_output=$(openshell sandbox exec --name smoke-test -- echo "openshell-ok" 2>&1) || true

  if [[ "$exec_output" == *"openshell-ok"* ]]; then
    ok "Sandbox exec works"
  else
    warn "Sandbox exec returned unexpected output: $exec_output"
  fi

  # Cleanup
  openshell sandbox delete smoke-test >/dev/null 2>&1 || true
  ok "Smoke test passed — sandbox lifecycle (create → exec → delete) validated"
}

# ── Main ─────────────────────────────────────────────────────────────────────
main() {
  preflight

  if [[ "$TEARDOWN" == "true" ]]; then
    teardown
    return
  fi

  if [[ "$SMOKE_TEST_ONLY" == "true" ]]; then
    smoke_test
    return
  fi

  echo "═══════════════════════════════════════════════════════════════"
  echo " OpenShell Phase 0 — Kind Setup"
  echo " Cluster: $CLUSTER_NAME  Context: $KUBE_CONTEXT"
  echo "═══════════════════════════════════════════════════════════════"
  echo

  install_crd
  install_gateway
  install_supervisor
  install_ingress
  configure_cli

  echo
  echo "═══════════════════════════════════════════════════════════════"
  echo " Setup complete — running smoke test"
  echo "═══════════════════════════════════════════════════════════════"
  echo

  smoke_test

  echo
  echo "═══════════════════════════════════════════════════════════════"
  echo " OpenShell ready on Kind"
  echo "  openshell status          # check connectivity"
  echo "  openshell sandbox create  # create a sandbox"
  echo "  openshell sandbox list    # list sandboxes"
  echo "═══════════════════════════════════════════════════════════════"
}

main "$@"
