#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
POLICY_FILE="$SCRIPT_DIR/bob_sandbox_policy.yaml"
DOCKERFILE="$SCRIPT_DIR/Dockerfile.sandbox"

export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$WORKSPACE_ROOT/.config}"

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
fi

# Wait for sandbox to reach Ready state
echo "Waiting for sandbox '$SANDBOX_NAME' to be ready..."
for i in $(seq 1 60); do
  PHASE=$("$OPENSHELL" sandbox list 2>&1 | sed 's/\x1b\[[0-9;]*m//g' | grep -w "$SANDBOX_NAME" | awk '{print $NF}')
  if [[ "$PHASE" == "Ready" ]]; then
    # Confirm exec actually works (list can report Ready before exec is available)
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
# (common when project was copied from a local macOS sandbox with symlinks
# pointing outside the workspace, e.g. uv cache links)
find "$DIR" -type l ! -exec test -e {} \; -delete 2>/dev/null || true

echo "Uploading files from $DIR to sandbox:$DIR ..."
run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- mkdir -p "$DIR"
run_cmd "$OPENSHELL" sandbox upload "$SANDBOX_NAME" "$DIR" "$DIR"
if [[ -d "$DIR/.claude" ]]; then
  echo "  Uploading .claude/ (gitignored, using --no-git-ignore)..."
  run_cmd "$OPENSHELL" sandbox upload --no-git-ignore "$SANDBOX_NAME" "$DIR/.claude" "$DIR/"
fi
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

# Suppress Node.js proxy and deprecation warnings from OpenShell's HTTP proxy interception
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q "NODE_NO_WARNINGS" "$BASHRC" 2>/dev/null; then
  echo "  Adding NODE_NO_WARNINGS..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'export NODE_NO_WARNINGS=1\n' >> $BASHRC"
fi

# Append ANTHROPIC_MODEL if missing
if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q "ANTHROPIC_MODEL" "$BASHRC" 2>/dev/null; then
  echo "  Adding ANTHROPIC_MODEL=$MODEL..."
  run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
    sh -c "printf 'export ANTHROPIC_MODEL=\"$MODEL\"\n' >> $BASHRC"
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

# Append BOBSHELL_API_KEY if missing (required for Bob shell)
if [[ -n "${BOBSHELL_API_KEY:-}" ]]; then
  if ! run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- grep -q "BOBSHELL_API_KEY" "$BASHRC" 2>/dev/null; then
    echo "  Adding BOBSHELL_API_KEY..."
    run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- \
      sh -c "printf 'export BOBSHELL_API_KEY=\"$BOBSHELL_API_KEY\"\n' >> $BASHRC"
  fi
else
  echo "  WARNING: BOBSHELL_API_KEY not set in local environment; Bob may not authenticate." >&2
fi

echo "Verifying .bashrc..."
run_cmd "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" -- cat "$BASHRC"

# ---------------------------------------------------------------------------
# Step 6: Install Bob shell
# ---------------------------------------------------------------------------

echo ""
echo "Installing Bob shell..."

exec_sb() {
  "$OPENSHELL" sandbox exec --name "$SANDBOX_NAME" --no-tty -- bash -c "$1"
}

if exec_sb 'export PATH="/sandbox/.npm-global/bin:$PATH" && command -v bob >/dev/null 2>&1' 2>/dev/null; then
  BOB_VERSION=$(exec_sb 'export PATH="/sandbox/.npm-global/bin:$PATH" && bob --version 2>/dev/null | tail -1 | tr -d "[:space:]"' 2>/dev/null || echo "unknown")
  echo "  Bob already installed (version: $BOB_VERSION)."
else
  echo "  Setting up npm global prefix..."
  exec_sb 'mkdir -p /sandbox/.npm-global && npm config set prefix /sandbox/.npm-global' 2>/dev/null

  # Ensure .npm-global/bin is on PATH in .bashrc
  if ! exec_sb 'grep -q ".npm-global/bin" /sandbox/.bashrc' 2>/dev/null; then
    exec_sb 'echo "export PATH=\"/sandbox/.npm-global/bin:\$PATH\"" >> /sandbox/.bashrc'
  fi

  echo "  Downloading Bob tarball..."
  if exec_sb 'curl -sfL https://s3.us-south.cloud-object-storage.appdomain.cloud/bobshell/bobshell-latest.tgz -o /tmp/bobshell.tgz && tar tzf /tmp/bobshell.tgz >/dev/null 2>&1' 2>/dev/null; then
    echo "  Extracting..."
    exec_sb 'mkdir -p /sandbox/.npm-global/lib/node_modules/bobshell && tar xzf /tmp/bobshell.tgz -C /sandbox/.npm-global/lib/node_modules/bobshell --strip-components=1'
    exec_sb 'mkdir -p /sandbox/.npm-global/bin && ln -sf ../lib/node_modules/bobshell/bundle/bob.js /sandbox/.npm-global/bin/bob && chmod +x /sandbox/.npm-global/lib/node_modules/bobshell/bundle/bob.js'
    exec_sb 'rm -f /tmp/bobshell.tgz'
  else
    echo "  Direct download failed (403 or network issue). Trying local bundle..."
    # Fall back: bundle from local workspace if setup-bob-sandbox was run before
    BOB_LOCAL="$WORKSPACE_ROOT/.cache/bobshell-latest.tgz"
    if [[ -f "$BOB_LOCAL" ]]; then
      run_cmd "$OPENSHELL" sandbox upload "$SANDBOX_NAME" "$BOB_LOCAL" /sandbox/
      exec_sb 'mkdir -p /sandbox/.npm-global/lib/node_modules/bobshell && tar xzf /sandbox/bobshell-latest.tgz -C /sandbox/.npm-global/lib/node_modules/bobshell --strip-components=1'
      exec_sb 'mkdir -p /sandbox/.npm-global/bin && ln -sf ../lib/node_modules/bobshell/bundle/bob.js /sandbox/.npm-global/bin/bob && chmod +x /sandbox/.npm-global/lib/node_modules/bobshell/bundle/bob.js'
      exec_sb 'rm -f /sandbox/bobshell-latest.tgz'
    else
      echo "  WARNING: Could not install Bob. Download the tarball manually to $BOB_LOCAL" >&2
    fi
  fi

  # Verify
  if exec_sb 'export PATH="/sandbox/.npm-global/bin:$PATH" && command -v bob >/dev/null 2>&1' 2>/dev/null; then
    BOB_VERSION=$(exec_sb 'export PATH="/sandbox/.npm-global/bin:$PATH" && bob --version 2>/dev/null | tail -1 | tr -d "[:space:]"' 2>/dev/null || echo "unknown")
    echo "  Bob installed successfully (version: $BOB_VERSION)."
  else
    echo "  WARNING: Bob installation may have failed." >&2
  fi
fi

echo ""
echo "Done. Connect with:"
echo "  $OPENSHELL sandbox connect $SANDBOX_NAME"
