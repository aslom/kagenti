#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
POLICY_FILE="$SCRIPT_DIR/litellm_sandbox_policy.yaml"
DOCKERFILE="$SCRIPT_DIR/Dockerfile.sandbox"

export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$WORKSPACE_ROOT/.config}"

run_cmd() {
  echo "+ $*" >&2
  "$@"
}

OPENSHELL="${OPENSHELL_BIN:-openshell}"
if ! command -v "$OPENSHELL" &>/dev/null; then
  OPENSHELL="$HOME/.local/bin/openshell"
  if ! command -v "$OPENSHELL" &>/dev/null; then
    echo "error: openshell not found in PATH or ~/.local/bin/" >&2
    echo "Install with: uv tool install -U openshell" >&2
    exit 1
  fi
fi

# ---------------------------------------------------------------------------
# Step 1: Ensure litellm provider exists
# ---------------------------------------------------------------------------

echo "Checking for litellm provider..."
if run_cmd "$OPENSHELL" provider list 2>&1 | grep -q "litellm"; then
  echo "  litellm provider found."
else
  echo "  litellm provider not found, creating..."
  if [[ -z "${ANTHROPIC_AUTH_TOKEN:-}" ]]; then
    echo "error: ANTHROPIC_AUTH_TOKEN is not set." >&2
    echo "Export it before running this script:" >&2
    echo "  export ANTHROPIC_AUTH_TOKEN=<your-token>" >&2
    exit 1
  fi
  run_cmd "$OPENSHELL" provider create \
    --name litellm \
    --type generic \
    --credential "ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN"
  echo "  litellm provider created."
fi

# ---------------------------------------------------------------------------
# Step 2: Determine sandbox name from current directory, verify .claude exists
# ---------------------------------------------------------------------------

DIR="$(pwd -P)"
SANDBOX_NAME="$(basename "$DIR")"

if [[ ! -d "$DIR/.claude" ]]; then
  echo "error: no .claude directory found in $DIR" >&2
  echo "Run this script from a project directory that has a .claude/ folder." >&2
  exit 1
fi
echo "Project directory: $DIR"
echo "Sandbox name: $SANDBOX_NAME"

# ---------------------------------------------------------------------------
# Step 3: Create sandbox if it doesn't already exist
# ---------------------------------------------------------------------------

echo "Checking for existing sandbox '$SANDBOX_NAME'..."
if run_cmd "$OPENSHELL" sandbox list 2>&1 | grep -qw "$SANDBOX_NAME"; then
  echo "  Sandbox '$SANDBOX_NAME' already exists."
else
  echo "  Creating sandbox '$SANDBOX_NAME'..."
  if [[ ! -f "$POLICY_FILE" ]]; then
    echo "error: policy file not found: $POLICY_FILE" >&2
    exit 1
  fi
  run_cmd "$OPENSHELL" sandbox create \
    --name "$SANDBOX_NAME" \
    --from "$DOCKERFILE" \
    --policy "$POLICY_FILE" \
    --provider litellm \
    -- true
  echo "  Sandbox '$SANDBOX_NAME' created."
fi

# ---------------------------------------------------------------------------
# Step 4: Upload local files into the sandbox
# ---------------------------------------------------------------------------

echo "Uploading files from $DIR to sandbox:$DIR ..."
run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- mkdir -p "$DIR"
run_cmd "$OPENSHELL" sandbox upload "$SANDBOX_NAME" "$DIR" "$DIR"
echo "  Files uploaded."

# ---------------------------------------------------------------------------
# Step 5: Ensure .bashrc exists with required environment variables
# ---------------------------------------------------------------------------

SANDBOX_HOME="/sandbox"
BASHRC="$SANDBOX_HOME/.bashrc"

echo "Ensuring .bashrc has required settings..."

# Create .bashrc if it doesn't exist
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- test -f "$BASHRC" 2>/dev/null; then
  echo "  Creating $BASHRC..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "touch $BASHRC"
fi

# Append ANTHROPIC_BASE_URL if missing
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q "ANTHROPIC_BASE_URL" "$BASHRC" 2>/dev/null; then
  echo "  Adding ANTHROPIC_BASE_URL..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'export ANTHROPIC_BASE_URL=\"https://ete-litellm.ai-models.vpc-int.res.ibm.com\"\n' >> $BASHRC"
fi

# Append CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS if missing
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS" "$BASHRC" 2>/dev/null; then
  echo "  Adding CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1\n' >> $BASHRC"
fi

# Append export HOME=<uploaded dir> if missing
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q "export HOME=$DIR" "$BASHRC" 2>/dev/null; then
  echo "  Adding export HOME=$DIR..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'export HOME=$DIR\n' >> $BASHRC"
fi

# Append cd \$HOME if missing
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q 'cd \$HOME' "$BASHRC" 2>/dev/null; then
  echo "  Adding cd \$HOME..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'cd \$HOME\n' >> $BASHRC"
fi

echo "Verifying .bashrc..."
run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- cat "$BASHRC"

echo ""
echo "Done. Connect with:"
echo "  $OPENSHELL sandbox connect $SANDBOX_NAME"
