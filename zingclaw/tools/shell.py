"""Shell execution tool."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from zingclaw.security import WORKSPACE_BOUNDARY_NOTE, WorkspaceAccessPolicy
from zingclaw.tools.base import Tool, tool_parameters
from zingclaw.tools.exec_session import (
    DEFAULT_EXEC_SESSION_MANAGER,
    DEFAULT_YIELD_MS,
    MAX_OUTPUT_CHARS,
    MAX_YIELD_MS,
    _ExecRequestContextMixin,
    clamp_session_int,
    format_session_poll,
)
from zingclaw.tools.sandbox import available_backends, sandbox_backend_available, wrap_command

_IS_WINDOWS = sys.platform == "win32"


@dataclass(slots=True)
class _PreparedCommand:
    command: str
    cwd: str
    env: dict[str, str]
    timeout: int | None
    shell_program: str | None
    login: bool


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "cmd": {"type": "string", "description": "Compatibility alias for command"},
            "working_dir": {"type": "string", "description": "Optional working directory"},
            "workdir": {"type": "string", "description": "Compatibility alias for working_dir"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds for this command",
                "minimum": 1,
                "maximum": 600,
            },
            "shell": {
                "type": ["string", "null"],
                "description": "Optional shell binary on Unix: sh, bash, or zsh",
            },
            "login": {
                "type": ["boolean", "null"],
                "description": "Whether bash/zsh should use login shell semantics",
            },
            "yield_time_ms": {
                "type": ["integer", "null"],
                "description": (
                    "Optional milliseconds to wait before returning output. "
                    "When set, a still-running command returns a session_id that can be "
                    "polled or written to with write_stdin."
                ),
                "minimum": 0,
                "maximum": MAX_YIELD_MS,
            },
            "max_output_chars": {
                "type": ["integer", "null"],
                "description": "Maximum output characters to return",
                "minimum": 1000,
                "maximum": MAX_OUTPUT_CHARS,
            },
            "max_output_tokens": {
                "type": ["integer", "null"],
                "description": "Compatibility alias for max_output_chars",
                "minimum": 1000,
                "maximum": MAX_OUTPUT_CHARS,
            },
        },
        "additionalProperties": False,
    }
)
class ExecTool(Tool, _ExecRequestContextMixin):
    """Execute a shell command inside the current workspace boundary."""

    _scopes = {"core", "subagent"}
    config_key = "exec"
    _MAX_TIMEOUT = 600
    _DEFAULT_TIMEOUT = 60
    _DEFAULT_MAX_OUTPUT = 10_000
    _BENIGN_DEVICE_PATHS: frozenset[str] = frozenset(
        {
            "/dev/null",
            "/dev/zero",
            "/dev/full",
            "/dev/random",
            "/dev/urandom",
            "/dev/stdin",
            "/dev/stdout",
            "/dev/stderr",
            "/dev/tty",
        }
    )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        restrict = bool(getattr(ctx, "restrict_to_workspace", True))
        return cls(
            working_dir=str(Path(ctx.workspace)),
            timeout=cls._DEFAULT_TIMEOUT,
            restrict_to_workspace=restrict,
            sandbox=(
                str(getattr(getattr(ctx, "settings", None), "security", None).exec_sandbox)
                if getattr(getattr(ctx, "settings", None), "security", None) is not None
                else ""
            ),
            max_output_chars=int(getattr(ctx, "max_tool_result_chars", cls._DEFAULT_MAX_OUTPUT)),
        )

    def __init__(
        self,
        timeout: int = _DEFAULT_TIMEOUT,
        working_dir: str | None = None,
        deny_patterns: list[str] | None = None,
        allow_patterns: list[str] | None = None,
        restrict_to_workspace: bool = False,
        sandbox: str = "",
        path_prepend: str = "",
        path_append: str = "",
        allowed_env_keys: list[str] | None = None,
        max_output_chars: int = _DEFAULT_MAX_OUTPUT,
        session_manager: Any | None = None,
    ) -> None:
        _ExecRequestContextMixin.__init__(self)
        self.timeout = timeout
        self.working_dir = working_dir
        self.restrict_to_workspace = restrict_to_workspace
        self.sandbox = sandbox.strip().lower()
        self.path_prepend = path_prepend
        self.path_append = path_append
        self.allowed_env_keys = allowed_env_keys or []
        self.max_output_chars = max(1000, int(max_output_chars))
        self._session_manager = session_manager or DEFAULT_EXEC_SESSION_MANAGER
        self._workspace_root = Path(self.working_dir or os.getcwd()).expanduser().resolve(strict=False)
        self._workspace_access = WorkspaceAccessPolicy(
            workspace_root=self._workspace_root,
            restrict_to_workspace=self.restrict_to_workspace,
            allowed_root=self._workspace_root if self.restrict_to_workspace else None,
        )
        self.deny_patterns = (deny_patterns or []) + [
            r"\brm\s+-[rf]{1,2}\b",
            r"\bdel\s+/[fq]\b",
            r"\brmdir\s+/s\b",
            r"(?:^|[;&|]\s*)format(?!=)\b",
            r"\b(mkfs|diskpart)\b",
            r"\bdd\s+if=",
            r">\s*/dev/sd",
            r"\b(shutdown|reboot|poweroff)\b",
            r":\(\)\s*\{.*\};\s*:",
        ]
        self.allow_patterns = allow_patterns or []

    @property
    def name(self) -> str:
        return "exec"

    @property
    def description(self) -> str:
        return (
            "Execute a shell command and return its output. "
            "Use this for tests, builds, package commands, and git commands. "
            "Prefer read_file/find_files/grep for inspection and apply_patch/write_file/edit_file "
            "for code changes instead of shell hacks. "
            "For long-running or interactive commands, pass yield_time_ms; if the command keeps "
            "running, exec returns a session_id that can be polled or written to with write_stdin. "
            "Output is truncated and commands are limited to the workspace by default."
        )

    @property
    def exclusive(self) -> bool:
        return True

    async def execute(
        self,
        command: str | None = None,
        cmd: str | None = None,
        working_dir: str | None = None,
        workdir: str | None = None,
        timeout: int | None = None,
        shell: str | None = None,
        login: bool | None = None,
        yield_time_ms: int | None = None,
        max_output_chars: int | None = None,
        max_output_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        del kwargs
        command = command or cmd
        working_dir = working_dir or workdir
        if not command:
            return "Error: Missing command. Provide command or cmd."
        if max_output_chars is None:
            max_output_chars = max_output_tokens

        prepared = self._prepare_command(command, working_dir, timeout, shell, login)
        if isinstance(prepared, str):
            return prepared

        if yield_time_ms is not None:
            return await self._execute_session(prepared, yield_time_ms, max_output_chars)

        try:
            process = await self._spawn(
                prepared.command,
                prepared.cwd,
                prepared.env,
                prepared.shell_program,
                prepared.login,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=prepared.timeout,
                )
            except asyncio.TimeoutError:
                await self._kill_process(process)
                return f"Error: Command timed out after {prepared.timeout} seconds"
            except asyncio.CancelledError:
                await self._kill_process(process)
                raise

            output_parts: list[str] = []
            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")
            output_parts.append(f"\nExit code: {process.returncode}")
            result = "\n".join(output_parts) if output_parts else "(no output)"
            return self._truncate_output(
                result,
                limit=clamp_session_int(
                    max_output_chars,
                    self.max_output_chars,
                    1000,
                    MAX_OUTPUT_CHARS,
                ),
            )
        except Exception as exc:
            return f"Error executing command: {exc}"

    async def _execute_session(
        self,
        prepared: _PreparedCommand,
        yield_time_ms: int | None,
        max_output_chars: int | None,
    ) -> str:
        try:
            session_id, poll = await self._session_manager.start(
                command=prepared.command,
                cwd=prepared.cwd,
                env=prepared.env,
                timeout=prepared.timeout,
                shell_program=prepared.shell_program,
                login=prepared.login,
                yield_time_ms=clamp_session_int(yield_time_ms, DEFAULT_YIELD_MS, 0, MAX_YIELD_MS),
                max_output_chars=clamp_session_int(
                    max_output_chars,
                    self.max_output_chars,
                    1000,
                    MAX_OUTPUT_CHARS,
                ),
                owner_session_key=self._owner_session_key(),
            )
            return format_session_poll(session_id, poll)
        except Exception as exc:
            return f"Error executing command: {exc}"

    def _resolve_timeout(self, timeout: int | None) -> int | None:
        if timeout:
            return min(timeout, self._MAX_TIMEOUT)
        if self.timeout and self.timeout > 0:
            return self.timeout
        return None

    def _prepare_command(
        self,
        command: str,
        working_dir: str | None = None,
        timeout: int | None = None,
        shell: str | None = None,
        login: bool | None = None,
    ) -> _PreparedCommand | str:
        if self.restrict_to_workspace:
            try:
                cwd_path = self._workspace_access.validate_working_dir(working_dir)
            except Exception as exc:
                return f"Error: {exc}"
            cwd = str(cwd_path)
        else:
            try:
                cwd = str(self._workspace_access.validate_working_dir(working_dir))
            except Exception:
                return "Error: working_dir could not be resolved" + WORKSPACE_BOUNDARY_NOTE

        workspace_root = str(self._workspace_access.workspace_root)

        guard_error = self._guard_command(
            command,
            cwd,
            restrict_to_workspace=self.restrict_to_workspace,
            workspace_root=workspace_root,
        )
        if guard_error:
            return guard_error

        env = self._build_env()

        if self.sandbox:
            if _IS_WINDOWS:
                return f"Error: sandbox backend {self.sandbox!r} is not supported on Windows"
            if not sandbox_backend_available(self.sandbox):
                return (
                    f"Error: sandbox backend {self.sandbox!r} is not available on this system. "
                    f"Available backends: {', '.join(available_backends()) or '(none)'}"
                )
            try:
                command = wrap_command(self.sandbox, command, workspace_root, cwd)
            except ValueError as exc:
                return f"Error: {exc}"
            cwd = workspace_root

        if self.path_prepend or self.path_append:
            if _IS_WINDOWS:
                env["PATH"] = self._compose_path(env.get("PATH", ""))
            else:
                command = self._wrap_path_export(command, env)

        shell_program, shell_error = self._resolve_shell(shell)
        if shell_error:
            return shell_error

        return _PreparedCommand(
            command=command,
            cwd=cwd,
            env=env,
            timeout=self._resolve_timeout(timeout),
            shell_program=shell_program,
            login=True if login is None else login,
        )

    def _compose_path(self, current_path: str) -> str:
        parts: list[str] = []
        if self.path_prepend:
            parts.append(self.path_prepend)
        if current_path:
            parts.append(current_path)
        if self.path_append:
            parts.append(self.path_append)
        return os.pathsep.join(parts)

    def _wrap_path_export(self, command: str, env: dict[str, str]) -> str:
        segments: list[str] = []
        if self.path_prepend:
            env["ZINGCLAW_PATH_PREPEND"] = self.path_prepend
            segments.append("$ZINGCLAW_PATH_PREPEND")
        segments.append("$PATH")
        if self.path_append:
            env["ZINGCLAW_PATH_APPEND"] = self.path_append
            segments.append("$ZINGCLAW_PATH_APPEND")
        path_expr = os.pathsep.join(segments)
        return f'export PATH="{path_expr}"; {command}'

    @staticmethod
    async def _spawn(
        command: str,
        cwd: str,
        env: dict[str, str],
        shell_program: str | None = None,
        login: bool = True,
        *,
        stdin: int = asyncio.subprocess.DEVNULL,
    ) -> asyncio.subprocess.Process:
        if _IS_WINDOWS:
            if "\n" in command:
                return await asyncio.create_subprocess_exec(
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    command,
                    stdin=stdin,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
            return await asyncio.create_subprocess_shell(
                command,
                stdin=stdin,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

        shell_program = shell_program or shutil.which("bash") or "/bin/bash"
        args = [shell_program]
        shell_name = Path(shell_program).name.lower()
        if login and shell_name in {"bash", "bash.exe", "zsh", "zsh.exe"}:
            args.append("-l")
        args.extend(["-c", command])
        return await asyncio.create_subprocess_exec(
            *args,
            stdin=stdin,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

    @staticmethod
    def _resolve_shell(shell: str | None) -> tuple[str | None, str | None]:
        if not shell:
            return None, None
        if _IS_WINDOWS:
            return None, "Error: shell parameter is not supported on Windows"
        if "\0" in shell or "\n" in shell or "\r" in shell:
            return None, "Error: shell contains invalid characters"

        allowed = {"sh", "bash", "zsh"}
        path = Path(shell).expanduser()
        if path.is_absolute():
            if path.name not in allowed:
                return None, f"Error: unsupported shell {shell!r}. Allowed: bash, sh, zsh"
            if not path.is_file() or not os.access(path, os.X_OK):
                return None, f"Error: shell is not executable: {shell}"
            return str(path), None
        if "/" in shell or "\\" in shell:
            return None, "Error: shell must be a shell name or absolute path"
        if shell not in allowed:
            return None, f"Error: unsupported shell {shell!r}. Allowed: bash, sh, zsh"
        resolved = shutil.which(shell)
        if not resolved:
            return None, f"Error: shell not found: {shell}"
        return resolved, None

    @staticmethod
    async def _kill_process(process: asyncio.subprocess.Process) -> None:
        process.kill()
        try:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(process.wait(), timeout=5.0)
        finally:
            if not _IS_WINDOWS:
                try:
                    os.waitpid(process.pid, os.WNOHANG)
                except (ProcessLookupError, ChildProcessError) as exc:
                    logger.debug("Process already reaped or not found: {}", exc)

    def _build_env(self) -> dict[str, str]:
        if _IS_WINDOWS:
            system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
            env = {
                "SYSTEMROOT": system_root,
                "COMSPEC": os.environ.get("COMSPEC", f"{system_root}\\system32\\cmd.exe"),
                "USERPROFILE": os.environ.get("USERPROFILE", ""),
                "HOMEDRIVE": os.environ.get("HOMEDRIVE", "C:"),
                "HOMEPATH": os.environ.get("HOMEPATH", "\\"),
                "TEMP": os.environ.get("TEMP", f"{system_root}\\Temp"),
                "TMP": os.environ.get("TMP", f"{system_root}\\Temp"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "PATH": os.environ.get("PATH", f"{system_root}\\system32;{system_root}"),
                "PYTHONUNBUFFERED": "1",
            }
        else:
            env = {
                "HOME": os.environ.get("HOME", "/tmp"),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "TERM": os.environ.get("TERM", "dumb"),
                "PATH": os.environ.get("PATH", ""),
                "PYTHONUNBUFFERED": "1",
            }

        for key in self.allowed_env_keys:
            value = os.environ.get(key)
            if value is not None:
                env[key] = value
        return env

    def _truncate_output(self, result: str, *, limit: int | None = None) -> str:
        limit = self.max_output_chars if limit is None else max(1000, int(limit))
        if len(result) <= limit:
            return result
        half = limit // 2
        return (
            result[:half]
            + f"\n\n... ({len(result) - limit:,} chars truncated) ...\n\n"
            + result[-half:]
        )

    def _guard_command(
        self,
        command: str,
        cwd: str,
        *,
        restrict_to_workspace: bool | None = None,
        workspace_root: str | None = None,
    ) -> str | None:
        cmd = command.strip()
        lower = cmd.lower()

        explicitly_allowed = bool(self.allow_patterns) and any(
            re.search(pattern, lower) for pattern in self.allow_patterns
        )
        if not explicitly_allowed:
            for pattern in self.deny_patterns:
                if re.search(pattern, lower):
                    return "Error: Command blocked by deny pattern filter"
            if self.allow_patterns:
                return "Error: Command blocked by allowlist filter (not in allowlist)"

        should_restrict = self.restrict_to_workspace if restrict_to_workspace is None else restrict_to_workspace
        if should_restrict:
            if "..\\" in cmd or "../" in cmd:
                return (
                    "Error: Command blocked by safety guard (path traversal detected)"
                    + WORKSPACE_BOUNDARY_NOTE
                )

            cwd_path = Path(cwd).expanduser().resolve(strict=False)
            access = WorkspaceAccessPolicy(
                workspace_root=Path(workspace_root).expanduser().resolve(strict=False)
                if workspace_root
                else self._workspace_root,
                restrict_to_workspace=should_restrict,
                allowed_root=Path(workspace_root).expanduser().resolve(strict=False)
                if workspace_root and should_restrict
                else None,
            )

            for raw in self._extract_absolute_paths(cmd):
                try:
                    expanded = os.path.expandvars(raw.strip())
                    if self._is_benign_device_path(expanded):
                        continue
                    resolved = Path(expanded).expanduser().resolve(strict=False)
                except Exception:
                    continue

                if self._is_benign_device_path(str(resolved)):
                    continue

                if resolved.is_absolute() and not access.allows_command_path(resolved, cwd=cwd_path):
                    return (
                        "Error: Command blocked by safety guard (path outside working dir)"
                        + WORKSPACE_BOUNDARY_NOTE
                    )

        return None

    @classmethod
    def _is_benign_device_path(cls, path: str) -> bool:
        if path in cls._BENIGN_DEVICE_PATHS:
            return True
        return path.startswith("/dev/fd/")

    @staticmethod
    def _extract_absolute_paths(command: str) -> list[str]:
        win_paths = re.findall(
            r"(?<![A-Za-z])(?:[A-Za-z]:[^\s\"'|><;]*|\\\\[^\s\"'|><;]+(?:\\[^\s\"'|><;]+)*)",
            command,
        )
        posix_paths = re.findall(r"(?:^|[\s|>'\"])(/[^\s\"'>;|<]+)", command)
        home_paths = re.findall(r"(?:^|[\s>'\"])(~[^\s\"'>;|<]*)", command)
        return win_paths + posix_paths + home_paths
