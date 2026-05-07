#!/usr/bin/env -S uv run --with openshell --with click
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openshell",
#     "click>=8.0",
# ]
# ///
"""kosh - Kagenti OpenShell CLI wrapper.

Proxies all openshell subcommands (gateway, sandbox, status, etc.) and adds
kagenti-specific commands like ``teleport``.

Usage:
    uv run kosh.py gateway status
    uv run kosh.py sandbox list
    uv run kosh.py teleport my-sandbox
"""
from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys

import click


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    cwd = kwargs.get("cwd")
    env_overrides = kwargs.get("env")
    parts = [shlex.join(cmd)]
    if cwd:
        parts.append(f"(cwd={cwd})")
    if env_overrides:
        diff = {k: v for k, v in env_overrides.items() if os.environ.get(k) != v}
        if diff:
            env_str = " ".join(f"{k}={v}" for k, v in sorted(diff.items()))
            parts.append(f"(env: {env_str})")
    click.echo(f"+ {' '.join(parts)}", err=True)
    kwargs.setdefault("stdin", sys.stdin)
    kwargs.setdefault("stdout", sys.stdout)
    kwargs.setdefault("stderr", sys.stderr)
    return subprocess.run(cmd, **kwargs)


def _find_openshell() -> str:
    path = shutil.which("openshell")
    if path:
        return path
    click.echo("error: 'openshell' CLI not found in PATH", err=True)
    click.echo("Install it with: uv tool install -U openshell", err=True)
    sys.exit(1)


OPENSHELL_PASSTHROUGH = [
    "sandbox",
    "gateway",
    "status",
    "forward",
    "logs",
    "policy",
    "settings",
    "provider",
    "inference",
    "doctor",
    "term",
    "ssh-proxy",
]


class KoshGroup(click.Group):
    """Click group that delegates unknown commands to the openshell CLI."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        if cmd_name in OPENSHELL_PASSTHROUGH:
            return _make_passthrough(cmd_name)
        return None

    def list_commands(self, ctx: click.Context) -> list[str]:
        native = super().list_commands(ctx)
        return sorted(set(native + OPENSHELL_PASSTHROUGH))

    def format_usage(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write("Usage: kosh <command> [args...]\n")

    def format_help_text(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        formatter.write_paragraph()
        formatter.write("Kagenti OpenShell CLI — all openshell commands plus kagenti extras.\n")


def _make_passthrough(name: str) -> click.Command:
    @click.command(name, context_settings={"ignore_unknown_options": True, "allow_extra_args": True})
    @click.pass_context
    def proxy(ctx: click.Context) -> None:
        openshell = _find_openshell()
        result = _run([openshell, name, *ctx.args])
        sys.exit(result.returncode)

    proxy.help = f"(passthrough) openshell {name}"
    return proxy


@click.group(cls=KoshGroup)
@click.version_option(version="0.1.0", prog_name="kosh")
def cli() -> None:
    """kosh - Kagenti OpenShell CLI."""


SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
TELEPORT_SH = SCRIPT_DIR / "teleport.sh"
DEFAULT_MODEL = "aws/claude-opus-4-6"


@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]), default="zsh")
def completions(shell: str) -> None:
    """Generate shell completions for kosh.

    Prints a completion script to stdout. Add to your shell profile:

    \b
        # zsh — add to ~/.zshrc
        eval "$(kosh completions zsh)"
    \b
        # bash — add to ~/.bashrc
        eval "$(kosh completions bash)"
    \b
        # fish — add to fish config
        kosh completions fish | source
    """
    from click.shell_completion import get_completion_class

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        click.echo(f"error: unsupported shell: {shell}", err=True)
        sys.exit(1)
    comp = comp_cls(cli, {}, "kosh", "_KOSH_COMPLETE")
    click.echo(comp.source())


@cli.command()
@click.option("--directory", "-d", default=None, type=click.Path(exists=True, file_okay=False), help="Project directory to teleport (defaults to last local sandbox).")
@click.option("--openshell-bin", default=None, help="Path to openshell binary.")
@click.option("--xdg-config-home", default=None, help="Override XDG_CONFIG_HOME for gateway config.")
@click.option("--connect/--no-connect", default=False, help="Connect to the sandbox after setup.")
@click.option("--custom-image", is_flag=True, default=False, help="Build sandbox from Dockerfile.sandbox (requires Docker).")
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Claude model to set as ANTHROPIC_MODEL.")
def teleport(directory: str | None, openshell_bin: str | None, xdg_config_home: str | None, connect: bool, custom_image: bool, model: str) -> None:
    """Set up and sync a project into an OpenShell sandbox.

    Creates the litellm provider if needed, creates a sandbox named after the
    project directory, uploads local files, and configures .bashrc inside the
    sandbox. Delegates to teleport.sh.

    If no --directory is specified, uses the last local sandbox from kosh
    config. The directory basename is used as the openshell sandbox name.

    Examples:

        kosh teleport
        kosh teleport -d ~/projects/my-app
        kosh teleport --connect
    """
    if not TELEPORT_SH.exists():
        click.echo(f"error: teleport.sh not found at {TELEPORT_SH}", err=True)
        sys.exit(1)

    if directory:
        cwd = directory
    else:
        config_dir = _kosh_config_dir()
        last = _load_last_sandbox(config_dir)
        if last and pathlib.Path(last).is_dir():
            cwd = last
            click.echo(f"Using last local sandbox: {cwd}")
        else:
            click.echo("error: no --directory specified and no last local sandbox found.", err=True)
            click.echo("Create one first: kosh local-sandbox create --name <name>", err=True)
            sys.exit(1)

    cwd_path = pathlib.Path(cwd).resolve()
    sandbox_name = cwd_path.name

    env = os.environ.copy()
    if openshell_bin:
        env["OPENSHELL_BIN"] = openshell_bin
    if xdg_config_home:
        env["XDG_CONFIG_HOME"] = xdg_config_home
    if custom_image:
        env["KOSH_CUSTOM_IMAGE"] = "1"
    if model != DEFAULT_MODEL:
        env["KOSH_MODEL"] = model

    click.echo(f"Teleporting '{sandbox_name}' from {cwd_path}")
    result = _run(["bash", str(TELEPORT_SH)], cwd=str(cwd_path), env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

    if connect:
        openshell = openshell_bin or shutil.which("openshell") or os.path.expanduser("~/.local/bin/openshell")
        result = _run([openshell, "sandbox", "connect", sandbox_name], cwd=str(cwd_path), env=env)
        sys.exit(result.returncode)


SANDBOX_SH = SCRIPT_DIR / "sandbox.sh"


def _kosh_config_dir() -> pathlib.Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = pathlib.Path(xdg) if xdg else pathlib.Path.home() / ".config"
    return base / "kosh"


def _read_metadata(config_dir: pathlib.Path) -> dict:
    meta_file = config_dir / "metadata.json"
    if meta_file.exists():
        return json.loads(meta_file.read_text())
    return {"sandboxes": {}}


def _write_metadata(config_dir: pathlib.Path, metadata: dict) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    meta_file = config_dir / "metadata.json"
    meta_file.write_text(json.dumps(metadata, indent=2) + "\n")


def _save_last_sandbox(config_dir: pathlib.Path, sandbox_dir: pathlib.Path) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "last_local_sandbox").write_text(str(sandbox_dir) + "\n")


def _load_last_sandbox(config_dir: pathlib.Path) -> str | None:
    last_file = config_dir / "last_local_sandbox"
    if last_file.exists():
        return last_file.read_text().strip()
    return None


def _run_sandbox_sh(sandbox_dir: pathlib.Path) -> None:
    if not SANDBOX_SH.exists():
        click.echo(f"error: sandbox.sh not found at {SANDBOX_SH}", err=True)
        sys.exit(1)
    result = _run(["bash", str(SANDBOX_SH), "zsh"], cwd=str(sandbox_dir))
    sys.exit(result.returncode)


@cli.group("local-sandbox")
def local_sandbox() -> None:
    """Manage local macOS sandboxed environments."""



@local_sandbox.command()
@click.option("--name", required=True, help="Name for the local sandbox (used as directory name).")
@click.option("--model", default=DEFAULT_MODEL, show_default=True, help="Claude model to set as ANTHROPIC_MODEL.")
def create(name: str, model: str) -> None:
    """Create a local sandbox directory and launch a sandboxed shell.

    Creates the directory in the current working directory if it doesn't
    exist, registers it (with full path) in kosh metadata, and starts
    sandbox.sh zsh inside it.

    Examples:

        kosh local-sandbox create --name my-project
    """
    if not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        click.echo("error: ANTHROPIC_AUTH_TOKEN is not set.", err=True)
        click.echo("Export it before running this command:", err=True)
        click.echo("  export ANTHROPIC_AUTH_TOKEN=<your-token>", err=True)
        sys.exit(1)

    sandbox_dir = (pathlib.Path.cwd() / name).resolve()

    if not sandbox_dir.exists():
        click.echo(f"Creating directory {sandbox_dir}")
        sandbox_dir.mkdir(parents=True)
    else:
        click.echo(f"Directory {sandbox_dir} already exists.")

    rc_lines = [
        'export ANTHROPIC_BASE_URL="https://ete-litellm.ai-models.vpc-int.res.ibm.com"',
        "export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1",
        f'export ANTHROPIC_MODEL="{model}"',
    ]
    rc_content = "\n".join(rc_lines) + "\n"
    for rc_name in (".bashrc", ".zshrc"):
        rc_path = sandbox_dir / rc_name
        if not rc_path.exists():
            rc_path.write_text(rc_content)
            click.echo(f"Created {rc_path}")
        else:
            existing = rc_path.read_text()
            added = False
            for line in rc_lines:
                key = line.split("=")[0]
                if key not in existing:
                    with rc_path.open("a") as f:
                        f.write(line + "\n")
                    added = True
                elif "ANTHROPIC_MODEL" in key and f'"{model}"' not in existing:
                    new_text = "\n".join(
                        (line if l.startswith("export ANTHROPIC_MODEL=") else l)
                        for l in existing.splitlines()
                    ) + "\n"
                    rc_path.write_text(new_text)
                    added = True
            if added:
                click.echo(f"Updated {rc_path}")
            else:
                click.echo(f"{rc_path} already configured.")

    config_dir = _kosh_config_dir()

    metadata = _read_metadata(config_dir)
    if name not in metadata["sandboxes"]:
        metadata["sandboxes"][name] = {"directory": str(sandbox_dir)}
        _write_metadata(config_dir, metadata)
        click.echo(f"Registered sandbox '{name}' in {config_dir / 'metadata.json'}")
    else:
        click.echo(f"Sandbox '{name}' already registered.")

    _save_last_sandbox(config_dir, sandbox_dir)
    click.echo(f"Saved as last sandbox in {config_dir / 'last_local_sandbox'}")

    _run_sandbox_sh(sandbox_dir)


@local_sandbox.command()
@click.option("--name", default=None, help="Name of the local sandbox to connect to (defaults to last used).")
def connect(name: str | None) -> None:
    """Connect to an existing local sandbox.

    If --name is omitted, reconnects to the last sandbox used. Launches
    sandbox.sh zsh in the sandbox directory.

    Examples:

        kosh local-sandbox connect
        kosh local-sandbox connect --name my-project
    """
    config_dir = _kosh_config_dir()

    if name:
        metadata = _read_metadata(config_dir)
        entry = metadata.get("sandboxes", {}).get(name)
        if entry:
            sandbox_dir = pathlib.Path(entry["directory"])
        else:
            sandbox_base = pathlib.Path(os.environ.get("SANDBOX_DIR", pathlib.Path.home() / "sandbox"))
            sandbox_dir = (sandbox_base / name).resolve()
    else:
        last = _load_last_sandbox(config_dir)
        if not last:
            click.echo("error: no last sandbox found. Use --name or create one first.", err=True)
            sys.exit(1)
        sandbox_dir = pathlib.Path(last)

    if not sandbox_dir.is_dir():
        click.echo(f"error: sandbox directory does not exist: {sandbox_dir}", err=True)
        sys.exit(1)

    _save_last_sandbox(config_dir, sandbox_dir)
    click.echo(f"Connecting to local sandbox at {sandbox_dir}")

    _run_sandbox_sh(sandbox_dir)


@local_sandbox.command("list")
def list_sandboxes() -> None:
    """List all registered local sandboxes.

    Examples:

        kosh local-sandbox list
    """
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    sandboxes = metadata.get("sandboxes", {})
    last = _load_last_sandbox(config_dir)

    if not sandboxes:
        click.echo("No local sandboxes registered.")
        return

    name_w = max(len(n) for n in sandboxes)
    name_w = max(name_w, 4)
    click.echo(f"{'NAME':<{name_w}}  {'STATUS':<9}  DIRECTORY")
    for name, entry in sorted(sandboxes.items()):
        directory = entry.get("directory", "")
        exists = pathlib.Path(directory).is_dir()
        status = "exists" if exists else "missing"
        marker = " *" if last and pathlib.Path(last) == pathlib.Path(directory) else ""
        click.echo(f"{name:<{name_w}}  {status:<9}  {directory}{marker}")

    if last:
        click.echo(f"\n* = last used")


@local_sandbox.command()
@click.option("--name", required=True, help="Name of the local sandbox to delete.")
def delete(name: str) -> None:
    """Delete a local sandbox directory and remove it from metadata.

    Examples:

        kosh local-sandbox delete --name my-project
    """
    config_dir = _kosh_config_dir()
    metadata = _read_metadata(config_dir)
    entry = metadata.get("sandboxes", {}).get(name)

    if entry:
        sandbox_dir = pathlib.Path(entry["directory"])
    else:
        sandbox_base = pathlib.Path(os.environ.get("SANDBOX_DIR", pathlib.Path.home() / "sandbox"))
        sandbox_dir = (sandbox_base / name).resolve()

    if sandbox_dir.is_dir():
        click.confirm(f"Delete directory {sandbox_dir} and all its contents?", abort=True)
        import shutil as _shutil
        _shutil.rmtree(sandbox_dir)
        click.echo(f"Deleted {sandbox_dir}")
    else:
        click.echo(f"Directory {sandbox_dir} does not exist (skipping).")

    if name in metadata.get("sandboxes", {}):
        del metadata["sandboxes"][name]
        _write_metadata(config_dir, metadata)
        click.echo(f"Removed '{name}' from {config_dir / 'metadata.json'}")

    last = _load_last_sandbox(config_dir)
    if last and pathlib.Path(last) == sandbox_dir:
        (config_dir / "last_local_sandbox").unlink(missing_ok=True)
        click.echo("Cleared last_local_sandbox (was pointing to deleted sandbox).")

    click.echo(f"Sandbox '{name}' deleted.")


if __name__ == "__main__":
    cli(prog_name="kosh")
