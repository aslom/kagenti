#!/bin/bash
# setup-kosh-completions.sh — Set up shell completions for kosh CLI
#
# Auto-detects zsh or bash and installs completions accordingly.
#
# Usage:
#   ./setup-kosh-completions.sh
#
# Run again after kosh.py changes to regenerate completions.
set -euo pipefail

# --- Detect shell ---

detect_shell() {
  # Check parent process name first
  local parent_shell
  parent_shell=$(ps -o comm= -p $PPID 2>/dev/null | sed 's|.*/||' | sed 's/^-//' || true)
  case "$parent_shell" in
    zsh)  echo "zsh"; return ;;
    bash) echo "bash"; return ;;
  esac
  # Fall back to $SHELL
  case "${SHELL:-}" in
    */zsh)  echo "zsh" ;;
    */bash) echo "bash" ;;
    *)      echo "zsh" ;;  # default
  esac
}

DETECTED_SHELL=$(detect_shell)
echo "Detected shell: $DETECTED_SHELL"

# --- Find kosh.py path ---

find_kosh_py() {
  local rc_file="$1"

  # 1. Check if kosh alias is defined in rc file
  local alias_def
  alias_def=$(grep -E "^alias kosh=" "$rc_file" 2>/dev/null | tail -1 || true)
  if [[ -n "$alias_def" ]]; then
    local path
    path=$(echo "$alias_def" | sed -E 's/.*uv run ([^"]+).*/\1/' | sed 's/[" ]//g')
    if [[ -f "$path" ]]; then
      echo "$path"
      return
    fi
  fi

  # 2. Check if kosh function is defined in rc file
  local func_def
  func_def=$(grep -E "^kosh\(\)" "$rc_file" 2>/dev/null | tail -1 || true)
  if [[ -n "$func_def" ]]; then
    local path
    path=$(grep -A1 "^kosh()" "$rc_file" | grep -oE '/[^ "]+kosh\.py')
    if [[ -f "$path" ]]; then
      echo "$path"
      return
    fi
  fi

  # 3. Check for kosh.py in current directory
  if [[ -f "./kosh.py" ]]; then
    echo "$(pwd)/kosh.py"
    return
  fi

  # 4. Check script's own directory
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$script_dir/kosh.py" ]]; then
    echo "$script_dir/kosh.py"
    return
  fi

  return 1
}

# --- Setup for zsh ---

setup_zsh() {
  local rc_file="${HOME}/.zshrc"
  local zfunc_dir="${HOME}/.zfunc"
  local comp_file="${zfunc_dir}/_kosh"

  KOSH_PY=$(find_kosh_py "$rc_file") || {
    echo "error: Cannot find kosh.py. Set up the alias first:" >&2
    echo "  alias kosh=\"uv run /path/to/kosh.py\"" >&2
    exit 1
  }
  echo "Found kosh.py: $KOSH_PY"

  # Generate zsh completions
  mkdir -p "$zfunc_dir"
  echo "Generating zsh completions..."
  uv run "$KOSH_PY" completions zsh > "$comp_file"

  # Fix: replace the guard that checks for kosh in PATH
  sed -i '' 's/(( ! $+commands\[kosh\] )) && return 1/# alias\/function compatible/' "$comp_file" 2>/dev/null || \
  sed -i 's/(( ! $+commands\[kosh\] )) && return 1/# alias\/function compatible/' "$comp_file"

  # Fix: replace bare "kosh" invocation with full uv run path
  sed -i '' "s|COMP_CWORD=\$((CURRENT-1)) _KOSH_COMPLETE=zsh_complete kosh|COMP_CWORD=\$((CURRENT-1)) _KOSH_COMPLETE=zsh_complete uv run $KOSH_PY|" "$comp_file" 2>/dev/null || \
  sed -i "s|COMP_CWORD=\$((CURRENT-1)) _KOSH_COMPLETE=zsh_complete kosh|COMP_CWORD=\$((CURRENT-1)) _KOSH_COMPLETE=zsh_complete uv run $KOSH_PY|" "$comp_file"

  echo "Written: $comp_file"

  # Update .zshrc if needed
  local fpath_line='fpath=(~/.zfunc $fpath)'
  if ! grep -qF "$fpath_line" "$rc_file" 2>/dev/null; then
    echo "" >> "$rc_file"
    echo "# kosh CLI completions" >> "$rc_file"
    echo "$fpath_line" >> "$rc_file"
    echo "autoload -Uz compinit && compinit" >> "$rc_file"
    echo "Added fpath + compinit to $rc_file"
  else
    echo "fpath already configured in $rc_file"
  fi

  # Ensure kosh is defined as function (not just alias) for completion to work
  if ! grep -qE "^kosh\(\)" "$rc_file" 2>/dev/null; then
    if grep -qE "^alias kosh=" "$rc_file" 2>/dev/null; then
      echo "Note: Replacing alias with function (required for zsh completions)"
      sed -i '' "s|^alias kosh=.*|# & # replaced by function below|" "$rc_file" 2>/dev/null || \
      sed -i "s|^alias kosh=.*|# & # replaced by function below|" "$rc_file"
    fi
    echo "kosh() { uv run \"$KOSH_PY\" \"\$@\"; }" >> "$rc_file"
    echo "Added kosh() function to $rc_file"
  fi

  echo ""
  echo "Done! Reload your shell:"
  echo "  exec zsh"
}

# --- Setup for bash ---

setup_bash() {
  local rc_file="${HOME}/.bashrc"
  local comp_dir="${HOME}/.local/share/bash-completion/completions"
  local comp_file="${comp_dir}/kosh"

  KOSH_PY=$(find_kosh_py "$rc_file") || {
    # Also check .zshrc in case user has it there
    KOSH_PY=$(find_kosh_py "${HOME}/.zshrc") || {
      echo "error: Cannot find kosh.py. Set up the alias first:" >&2
      echo "  alias kosh=\"uv run /path/to/kosh.py\"" >&2
      exit 1
    }
  }
  echo "Found kosh.py: $KOSH_PY"

  # Generate bash completions
  mkdir -p "$comp_dir"
  echo "Generating bash completions..."
  uv run "$KOSH_PY" completions bash > "$comp_file"

  # Fix: replace bare "kosh" invocation with full uv run path
  sed -i '' "s|_KOSH_COMPLETE=bash_complete kosh|_KOSH_COMPLETE=bash_complete uv run $KOSH_PY|g" "$comp_file" 2>/dev/null || \
  sed -i "s|_KOSH_COMPLETE=bash_complete kosh|_KOSH_COMPLETE=bash_complete uv run $KOSH_PY|g" "$comp_file"

  echo "Written: $comp_file"

  # Ensure kosh alias/function exists in .bashrc
  if ! grep -qE "(^alias kosh=|^kosh\(\))" "$rc_file" 2>/dev/null; then
    echo "" >> "$rc_file"
    echo "# kosh CLI" >> "$rc_file"
    echo "alias kosh=\"uv run $KOSH_PY\"" >> "$rc_file"
    echo "Added kosh alias to $rc_file"
  fi

  # Source completions from .bashrc if not using bash-completion framework
  local source_line="source \"$comp_file\""
  if ! grep -qF "$comp_file" "$rc_file" 2>/dev/null; then
    # Check if bash-completion is loaded (it auto-discovers ~/.local/share/bash-completion/)
    if ! grep -qE "(bash.completion|bash_completion)" "$rc_file" 2>/dev/null; then
      echo "" >> "$rc_file"
      echo "# kosh completions" >> "$rc_file"
      echo "[ -f \"$comp_file\" ] && $source_line" >> "$rc_file"
      echo "Added completion source to $rc_file"
    else
      echo "bash-completion framework detected (will auto-load from $comp_dir)"
    fi
  fi

  echo ""
  echo "Done! Reload your shell:"
  echo "  source ~/.bashrc"
}

# --- Main ---

case "$DETECTED_SHELL" in
  zsh)  setup_zsh ;;
  bash) setup_bash ;;
  *)
    echo "error: Unsupported shell: $DETECTED_SHELL" >&2
    exit 1
    ;;
esac

echo ""
echo "To update completions after kosh.py changes:"
echo "  ./setup-kosh-completions.sh"
