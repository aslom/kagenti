#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
POLICY_FILE="$SCRIPT_DIR/litellm_sandbox_policy.yaml"
DOCKERFILE="$SCRIPT_DIR/Dockerfile.sandbox"

export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

MODEL="${KOSH_MODEL:-claude-opus-4-6}"

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
  CRED_ARGS=(--credential "ANTHROPIC_AUTH_TOKEN=$ANTHROPIC_AUTH_TOKEN")
  if [[ -n "${BOBSHELL_API_KEY:-}" ]]; then
    CRED_ARGS+=(--credential "BOBSHELL_API_KEY=$BOBSHELL_API_KEY")
  fi
  run_cmd "$OPENSHELL" provider create \
    --name litellm \
    --type generic \
    "${CRED_ARGS[@]}"
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
# Step 3: Create sandbox (+ one-time setup) or skip if already exists
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
  CREATE_ARGS=(
    --name "$SANDBOX_NAME"
    --policy "$POLICY_FILE"
    --provider litellm
  )
  if [[ "${KOSH_CUSTOM_IMAGE:-}" == "1" && -f "$DOCKERFILE" ]]; then
    echo "  Using custom Dockerfile: $DOCKERFILE"
    CREATE_ARGS+=(--from "$DOCKERFILE")
  fi
  run_cmd "$OPENSHELL" sandbox create "${CREATE_ARGS[@]}" --no-tty -- true || true
  echo "  Sandbox '$SANDBOX_NAME' created."

  # Wait for sandbox to reach Ready state before setup
  echo "  Waiting for sandbox to be ready..."
  for i in $(seq 1 60); do
    PHASE=$("$OPENSHELL" sandbox list 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -w "$SANDBOX_NAME" | awk '{print $NF}')
    if [[ "$PHASE" == "Ready" ]]; then
      if "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" --no-tty -- true 2>/dev/null; then
        echo "  Sandbox '$SANDBOX_NAME' is ready."
        break
      fi
    fi
    if [[ $i -eq 60 ]]; then
      echo "error: sandbox '$SANDBOX_NAME' did not become ready within 60s (phase: $PHASE)" >&2
      exit 1
    fi
    echo "  Waiting... (phase: $PHASE)"
    sleep 1
  done

  # --- One-time .bashrc setup (single batch write) ---
  echo "  Configuring sandbox environment..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- sh -c "cat >> /sandbox/.bashrc << 'KOSH_BASHRC'
export PATH=\"\$HOME/.local/bin:\$PATH\"
export PATH=\"/sandbox/.npm-global/bin:\$PATH\"
export ANTHROPIC_BASE_URL=\"https://ete-litellm.ai-models.vpc-int.res.ibm.com\"
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
export NODE_NO_WARNINGS=1
export ANTHROPIC_MODEL=\"$MODEL\"
export HOME=$DIR
cd \"$DIR\" 2>/dev/null || true
alias kosh=\"echo \\\"kosh is not available inside the sandbox. To run kosh commands, exit the sandbox (type exit) or use another terminal.\\\"\"
KOSH_BASHRC"

  # Add cd to .profile so login shells land in the right directory
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'cd \"$DIR\" 2>/dev/null || true\n' >> /sandbox/.profile"

  echo "  Environment configured."

  # --- One-time Bob shell install ---
  echo ""
  echo "  Installing Bob shell..."

  exec_sb() {
    "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" --no-tty -- bash -c "$1"
  }

  BOB_INSTALL_SH="$SCRIPT_DIR/bob-install.sh"
  if [[ ! -f "$BOB_INSTALL_SH" ]]; then
    echo "  ERROR: $BOB_INSTALL_SH not found" >&2
    echo "  Trying official installer as fallback..."
    exec_sb 'curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash' 2>/dev/null || true
  else
    run_cmd "$OPENSHELL" sandbox upload "$SANDBOX_NAME" "$BOB_INSTALL_SH" /tmp/
    if run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" --no-tty -- bash /tmp/bob-install.sh 2>/dev/null; then
      echo "  Bob installed."
    else
      echo "  WARNING: Bob install failed." >&2
      exec_sb 'curl -fsSL https://bob.ibm.com/download/bobshell.sh | bash' 2>/dev/null || true
    fi
  fi
fi

# Wait for sandbox to reach Ready state (for existing sandboxes that may be restarting)
echo "Waiting for sandbox '$SANDBOX_NAME' to be ready..."
for i in $(seq 1 60); do
  PHASE=$("$OPENSHELL" sandbox list 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -w "$SANDBOX_NAME" | awk '{print $NF}')
  if [[ "$PHASE" == "Ready" ]]; then
    if "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" --no-tty -- true 2>/dev/null; then
      echo "  Sandbox '$SANDBOX_NAME' is ready."
      break
    fi
  fi
  if [[ $i -eq 60 ]]; then
    echo "error: sandbox '$SANDBOX_NAME' did not become ready within 60s (phase: $PHASE)" >&2
    exit 1
  fi
  echo "  Waiting... (phase: $PHASE)"
  sleep 1
done

# ---------------------------------------------------------------------------
# Step 4: Upload local files into the sandbox
# ---------------------------------------------------------------------------

# Remove dangling symlinks that would cause upload to fail with EPERM
find "$DIR" -type l ! -exec test -e {} \; -delete 2>/dev/null || true

# Ensure .gitignore excludes sensitive files (tokens, certs, config)
GITIGNORE="$DIR/.gitignore"
SENSITIVE_PATTERNS=(
  ".config/"
  "openshell/"
  "oidc_token.json"
  "token.json"
  "edge_token.json"
  "rossconfig.json"
  "*.key"
  "*.crt"
  "*.pem"
)
_gitignore_changed=0
for _pat in "${SENSITIVE_PATTERNS[@]}"; do
  if ! grep -qxF "$_pat" "$GITIGNORE" 2>/dev/null; then
    echo "$_pat" >> "$GITIGNORE"
    _gitignore_changed=1
  fi
done
if [[ $_gitignore_changed -eq 1 ]]; then
  echo "  Updated .gitignore to exclude sensitive files from upload."
fi

# Warn about sensitive files/dirs that will be skipped
for _pat in "${SENSITIVE_PATTERNS[@]}"; do
  if [[ "$_pat" == */ ]]; then
    _dir="${_pat%/}"
    if [[ -d "$DIR/$_dir" ]]; then
      echo "  SKIP (sensitive): $DIR/$_dir/"
    fi
  elif [[ "$_pat" == \** ]]; then
    _found=$(find "$DIR" -maxdepth 1 -name "$_pat" 2>/dev/null | head -5)
    for _f in $_found; do
      echo "  SKIP (sensitive): $_f"
    done
  else
    if [[ -f "$DIR/$_pat" ]]; then
      echo "  SKIP (sensitive): $DIR/$_pat"
    fi
  fi
done

echo "Uploading files from $DIR to sandbox:$DIR ..."
run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- mkdir -p "$DIR"
run_cmd "$OPENSHELL" sandbox upload "$SANDBOX_NAME" "$DIR" "$DIR"
if [[ -d "$DIR/.claude" ]]; then
  echo "  Uploading .claude/ (gitignored, using --no-git-ignore)..."
  run_cmd "$OPENSHELL" sandbox upload --no-git-ignore "$SANDBOX_NAME" "$DIR/.claude" "$DIR/"
fi
echo "  Files uploaded."

echo ""
echo "Done. Sandbox '$SANDBOX_NAME' is ready."
echo ""
echo "To connect:"
echo "  kosh sandbox connect $SANDBOX_NAME"
