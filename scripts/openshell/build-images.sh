#!/usr/bin/env bash
# ============================================================================
# OPENSHELL LOCAL IMAGE BUILD
# ============================================================================
# Builds OpenShell images locally and optionally loads them into a Kind cluster.
# This script is for LOCAL DEVELOPMENT ONLY — production deployments pull
# pre-built images from ghcr.io/kagenti/.
#
# Usage:
#   scripts/openshell/build-images.sh                    # Build all images
#   scripts/openshell/build-images.sh --kind <cluster>   # Build + load into Kind
#   scripts/openshell/build-images.sh --gateway-only     # Build gateway only
#   scripts/openshell/build-images.sh --help             # Show usage
#
# Prerequisites:
#   - Docker (with buildx)
#   - Source repos cloned (see REPOS_DIR below)
#   - Kind (optional, for --kind flag)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Where the forked repos are cloned (override with OPENSHELL_REPOS_DIR)
REPOS_DIR="${OPENSHELL_REPOS_DIR:-$REPO_ROOT/../}"

# Image names (match ghcr.io paths for tag compatibility)
GATEWAY_IMAGE="ghcr.io/kagenti/openshell/gateway"
COMPUTE_DRIVER_IMAGE="ghcr.io/kagenti/openshell-driver-openshift/compute-driver"
CREDENTIALS_DRIVER_IMAGE="ghcr.io/kagenti/openshell-credentials-keycloak/credentials-driver"

TAG="${OPENSHELL_IMAGE_TAG:-local}"

# ── Flags ─────────────────────────────────────────────────────────────────────
KIND_CLUSTER=""
GATEWAY_ONLY=false
DRIVER_ONLY=false
CREDENTIALS_ONLY=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

LOCAL DEVELOPMENT ONLY — builds OpenShell images from source repos.
Production deployments pull pre-built images from ghcr.io/kagenti/.

Options:
  --kind <cluster>       Load built images into the named Kind cluster
  --gateway-only         Build only the gateway image
  --driver-only          Build only the compute driver image
  --credentials-only     Build only the credentials driver image
  --tag <tag>            Image tag (default: local)
  --repos-dir <path>     Directory containing source repos (default: $REPOS_DIR)
  --help                 Show this help message

Environment variables:
  OPENSHELL_REPOS_DIR    Override repos directory
  OPENSHELL_IMAGE_TAG    Override image tag (default: local)

Source repos expected at:
  \$REPOS_DIR/OpenShell/                       (gateway)
  \$REPOS_DIR/openshell-driver-openshift/      (compute driver)
  \$REPOS_DIR/openshell-credentials-keycloak/  (credentials driver)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kind)
            KIND_CLUSTER="$2"; shift 2 ;;
        --gateway-only)
            GATEWAY_ONLY=true; shift ;;
        --driver-only)
            DRIVER_ONLY=true; shift ;;
        --credentials-only)
            CREDENTIALS_ONLY=true; shift ;;
        --tag)
            TAG="$2"; shift 2 ;;
        --repos-dir)
            REPOS_DIR="$2"; shift 2 ;;
        --help|-h)
            usage; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

# ── Build functions ───────────────────────────────────────────────────────────

build_gateway() {
    local src="$REPOS_DIR/OpenShell"
    if [[ ! -d "$src" ]]; then
        echo "ERROR: Gateway source not found at $src" >&2
        echo "Clone it: git clone https://github.com/kagenti/OpenShell.git -b mvp $src" >&2
        return 1
    fi
    echo "Building gateway image: $GATEWAY_IMAGE:$TAG"
    docker build -t "$GATEWAY_IMAGE:$TAG" \
        --target gateway \
        -f "$src/deploy/docker/Dockerfile.images" \
        "$src"
}

build_compute_driver() {
    local src="$REPOS_DIR/openshell-driver-openshift"
    if [[ ! -d "$src" ]]; then
        echo "ERROR: Compute driver source not found at $src" >&2
        echo "Clone it: git clone https://github.com/kagenti/openshell-driver-openshift.git -b mvp $src" >&2
        return 1
    fi
    echo "Building compute driver image: $COMPUTE_DRIVER_IMAGE:$TAG"
    docker build -t "$COMPUTE_DRIVER_IMAGE:$TAG" \
        -f "$src/deploy/Dockerfile" \
        "$src"
}

build_credentials_driver() {
    local src="$REPOS_DIR/openshell-credentials-keycloak"
    if [[ ! -d "$src" ]]; then
        echo "ERROR: Credentials driver source not found at $src" >&2
        echo "Clone it: git clone https://github.com/kagenti/openshell-credentials-keycloak.git $src" >&2
        return 1
    fi
    echo "Building credentials driver image: $CREDENTIALS_DRIVER_IMAGE:$TAG"
    docker build -t "$CREDENTIALS_DRIVER_IMAGE:$TAG" \
        -f "$src/deploy/Dockerfile" \
        "$src"
}

kind_load() {
    local image="$1"
    echo "Loading $image:$TAG into Kind cluster '$KIND_CLUSTER'"
    kind load docker-image "$image:$TAG" --name "$KIND_CLUSTER"
}

# ── Main ──────────────────────────────────────────────────────────────────────

IMAGES_BUILT=()

if [[ "$GATEWAY_ONLY" == "true" ]]; then
    build_gateway
    IMAGES_BUILT+=("$GATEWAY_IMAGE")
elif [[ "$DRIVER_ONLY" == "true" ]]; then
    build_compute_driver
    IMAGES_BUILT+=("$COMPUTE_DRIVER_IMAGE")
elif [[ "$CREDENTIALS_ONLY" == "true" ]]; then
    build_credentials_driver
    IMAGES_BUILT+=("$CREDENTIALS_DRIVER_IMAGE")
else
    build_gateway
    IMAGES_BUILT+=("$GATEWAY_IMAGE")
    build_compute_driver
    IMAGES_BUILT+=("$COMPUTE_DRIVER_IMAGE")
    build_credentials_driver
    IMAGES_BUILT+=("$CREDENTIALS_DRIVER_IMAGE")
fi

if [[ -n "$KIND_CLUSTER" ]]; then
    for img in "${IMAGES_BUILT[@]}"; do
        kind_load "$img"
    done
fi

echo ""
echo "Done. Built images:"
for img in "${IMAGES_BUILT[@]}"; do
    echo "  $img:$TAG"
done
if [[ -n "$KIND_CLUSTER" ]]; then
    echo "Loaded into Kind cluster: $KIND_CLUSTER"
fi
