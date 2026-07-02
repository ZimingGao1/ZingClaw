"""Sandbox backends for shell command execution."""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path


def _resolve_sandbox_cwd(workspace: Path, cwd: str) -> str:
    try:
        return str(workspace / Path(cwd).expanduser().resolve().relative_to(workspace))
    except ValueError:
        return str(workspace)


def _bwrap_base(command: str, workspace: str, cwd: str, *, unshare_net: bool) -> str:
    """Wrap a command in a bubblewrap sandbox limited to the workspace."""

    ws = Path(workspace).expanduser().resolve()
    sandbox_cwd = _resolve_sandbox_cwd(ws, cwd)

    required = ["/usr"]
    optional = [
        "/bin",
        "/lib",
        "/lib64",
        "/etc/alternatives",
        "/etc/ssl/certs",
        "/etc/resolv.conf",
        "/etc/ld.so.cache",
    ]

    args = ["bwrap", "--new-session", "--die-with-parent", "--setenv", "HOME", str(ws)]
    if unshare_net:
        args.append("--unshare-net")
    for path in required:
        args += ["--ro-bind", path, path]
    for path in optional:
        args += ["--ro-bind-try", path, path]
    args += [
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        str(ws.parent),
        "--dir",
        str(ws),
        "--bind",
        str(ws),
        str(ws),
        "--chdir",
        sandbox_cwd,
        "--",
        "sh",
        "-c",
        command,
    ]
    return shlex.join(args)


def _bwrap(command: str, workspace: str, cwd: str) -> str:
    return _bwrap_base(command, workspace, cwd, unshare_net=False)


def _bwrap_net(command: str, workspace: str, cwd: str) -> str:
    """Wrap a command in bubblewrap with network access disabled."""

    return _bwrap_base(command, workspace, cwd, unshare_net=True)


_BACKENDS = {"bwrap": _bwrap, "bwrap_net": _bwrap_net}


def available_backends() -> list[str]:
    """Return known sandbox backend names."""

    return sorted(_BACKENDS)


def sandbox_backend_available(name: str) -> bool:
    """Return whether the named sandbox backend is available on this system."""

    if name not in _BACKENDS:
        return False
    if name in {"bwrap", "bwrap_net"}:
        return shutil.which("bwrap") is not None
    return False


def wrap_command(sandbox: str, command: str, workspace: str, cwd: str) -> str:
    """Wrap *command* using the named sandbox backend."""

    backend = _BACKENDS.get(sandbox)
    if backend is None:
        raise ValueError(f"Unknown sandbox backend {sandbox!r}. Available: {available_backends()}")
    return backend(command, workspace, cwd)
