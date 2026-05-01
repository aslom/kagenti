# Kagenti Installation on OpenShift


## Requirements

| Tool | Version |
|------|---------|
| `oc` / `kubectl` | `oc` ≥ 4.16.0 or `kubectl` ≥ 1.32.1 |
| `helm` | ≥ 3.18.0, < 4 |
| `python3` | ≥ 3.8 |
| OpenShift cluster | ≥ 4.19.0 recommended, with cluster-admin |

> **Note**: If your cluster already has a cert-manager installation (e.g. installed via the Red Hat
> OpenShift cert-manager Operator), remove it before running the installer — Kagenti installs its own.

## OpenShift Version Compatibility

| OCP Version | SPIRE Installation |
|-------------|-------------------|
| **4.19.0+** | ZTWIM Operator (OLM-managed) — recommended |
| **4.16.0 – 4.18.x** | Upstream SPIRE Helm charts (same functionality, no OLM) |
| **< 4.16.0** | Not supported |

## Installation

```bash
git clone https://github.com/kagenti/kagenti.git
cd kagenti

oc login https://api.your-cluster.example.com:6443 -u kubeadmin -p <password>

# Default: clones latest main from GitHub
./scripts/ocp/setup-kagenti.sh

# Use a local repository clone
./scripts/ocp/setup-kagenti.sh --kagenti-repo .

# Use a specific GitHub fork or branch
./scripts/ocp/setup-kagenti.sh --kagenti-repo https://github.com/my-org/kagenti.git

# Use a custom operator image (e.g. a dev build)
./scripts/ocp/setup-kagenti.sh --operator-image quay.io/my-org/kagenti-operator:dev
```

### Common Flags

| Flag | Description |
|------|-------------|
| `--kagenti-repo PATH\|URL` | Local path or GitHub URL (default: clones `main` to `~/.cache/kagenti`) |
| `--realm REALM` | Keycloak realm (default: `kagenti`) |
| `--skip-ovn-patch` | Skip OVN `routingViaHost` patch |
| `--skip-mcp-gateway` | Skip MCP Gateway installation |
| `--skip-ui` | Skip UI and backend installation |
| `--skip-mlflow` | Skip MLflow integration |
| `--operator-image IMG:TAG` | Custom operator image |
| `--dry-run` | Show commands without executing |

## Post-Installation

### Verify SPIRE Daemonsets

```bash
kubectl get daemonsets -n zero-trust-workload-identity-manager
```

If `Current` or `Ready` is `0`, see [Troubleshooting](#spire-daemonset-does-not-start).

### Get Keycloak Credentials

```bash
kubectl get secret keycloak-initial-admin -n keycloak \
  -o go-template='Username: {{.data.username | base64decode}}  Password: {{.data.password | base64decode}}{{"\n"}}'
```

### Access the UI

```bash
echo "https://$(kubectl get route kagenti-ui -n kagenti-system -o jsonpath='{.status.ingress[0].host}')"
```

If your cluster uses self-signed route certificates, open the URL and accept the certificate in your browser.

### MCP Inspector Configuration

Accept the proxy certificate so the MCP Inspector can establish a trusted connection:

```bash
echo "https://$(kubectl get route mcp-proxy -n kagenti-system -o jsonpath='{.status.ingress[0].host}')"
```

Open the printed URL in your browser and accept the certificate (a `Cannot GET /` response is expected). Then in the UI:

1. Navigate to **MCP Gateway → Configuration**
2. Set **Connection Type** to `via proxy`
3. Set **Inspector Proxy Address** to the URL above
4. Click **Test connection**

*These settings are persisted in your browser and only need to be configured once.*

## Troubleshooting

### SPIRE Daemonset Does Not Start

Check for SCC errors:

```bash
kubectl describe daemonsets -n zero-trust-workload-identity-manager spire-agent
kubectl describe daemonsets -n zero-trust-workload-identity-manager spire-spiffe-csi-driver
```

If events show `unable to validate against any security context constraint`:

```bash
oc adm policy add-scc-to-user privileged -z spire-agent -n zero-trust-workload-identity-manager
kubectl rollout restart daemonsets -n zero-trust-workload-identity-manager spire-agent

oc adm policy add-scc-to-user privileged -z spire-spiffe-csi-driver -n zero-trust-workload-identity-manager
kubectl rollout restart daemonsets -n zero-trust-workload-identity-manager spire-spiffe-csi-driver
```


### Upgrade from OCP 4.18 to 4.19

<details>
<summary>Red Hat OpenShift Container Platform (AWS)</summary>

```bash
oc patch clusterversion version --type merge -p '{"spec":{"channel":"fast-4.19"}}'

oc -n openshift-config patch cm admin-acks \
  --patch '{"data":{"ack-4.18-kube-1.32-api-removals-in-4.19":"true"}}' --type=merge
oc -n openshift-config patch cm admin-acks \
  --patch '{"data":{"ack-4.18-boot-image-opt-out-in-4.19":"true"}}' --type=merge

oc adm upgrade --to-latest=true --allow-not-recommended=true

oc get clusterversion
```

</details>

<details>
<summary>Single Node OpenShift</summary>

Ensure the instance has at least 24 cores and 64 Gi RAM.

```bash
oc patch clusterversion version --type merge -p '{"spec":{"channel":"stable-4.19"}}'

oc -n openshift-config patch cm admin-acks \
  --patch '{"data":{"ack-4.18-kube-1.32-api-removals-in-4.19":"true"}}' --type=merge

oc adm upgrade --to-latest=true --allow-not-recommended=true

oc get clusterversion
```

</details>

After upgrading, re-run `./scripts/ocp/setup-kagenti.sh` to install SPIRE via the ZTWIM operator (OLM-managed, available on 4.19+) instead of the upstream Helm charts.
