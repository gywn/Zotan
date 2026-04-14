"""Bash execution tools for Zotan agent."""

import asyncio
import contextlib
from contextlib import ExitStack
from typing import Any, cast

from pydantic_ai import RunContext, Tool

from ..config import WORKING_MODE, WORKSPACE
from ..text import truncate_text_by_tokens
from ..types_ import MainRunContext, ToolExecutionError


def _get_podman_container_name(ctx: RunContext[MainRunContext]) -> str:
    return f"zotan_{ctx.deps.agent_kind}"


async def _ensure_podman_container(ctx: RunContext[MainRunContext]) -> None:
    if ctx.metadata is None:
        ctx.metadata = dict()

    if (
        # Bash commands can be executed in the host environment
        WORKING_MODE == "container"
        # Podman container is already created
        or "bash" in ctx.metadata
    ):
        return

    proc = await asyncio.create_subprocess_exec(
        "podman", "run",
        "--name", (container_name := _get_podman_container_name(ctx)),
        "--rm",
        "--detach",
        "--volume", f"{ctx.deps.workspace_dir}:{WORKSPACE}",
        "zotan:python",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,  # fmt: skip
    )
    await proc.wait()

    ctx.metadata["bash"] = container_name


async def _remove_podman_container(ctx: RunContext[MainRunContext]) -> None:
    if (
        # Bash commands can be executed in the host environment
        WORKING_MODE == "container"
        # Podman container not created
        or ctx.metadata is None
        or "bash" not in ctx.metadata
    ):
        return

    proc = await asyncio.create_subprocess_exec(
        "podman", "kill", str(ctx.metadata["bash"]),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,  # fmt: skip
    )
    await proc.wait()

    proc = await asyncio.create_subprocess_exec(
        "podman", "wait", str(ctx.metadata["bash"]),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,  # fmt: skip
    )
    await proc.wait()

    del ctx.metadata["bash"]


async def _bash(
    ctx: RunContext[MainRunContext],
    command: str,
    intent: str,
    timeout: int = 5,
) -> str:
    """Execute a bash shell command and returns its output.

    - The command runs in the workspace directory.
    - ALWAYS execute commands that require root privileges using `sudo` because you have sudoer privilege
    - Install missing system dependencies using APT
    - Install missing Python dependencies using PIP
    - Do NOT use Python virtual environments, as you are working in a dedicated, isolated container environment

    Args:
        command: The shell command to execute.
        intent: A description of what information you're looking for from the command output.
        timeout: Maximum execution time in seconds (default: 5, max: 300).

    Returns:
        Command output including exit code, stdout, and stderr.
    """
    await _ensure_podman_container(ctx)

    if WORKING_MODE == "container":
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=ctx.deps.workspace_dir,
        )
    else:
        proc = await asyncio.create_subprocess_exec(
            "podman", "exec",
            "--workdir", WORKSPACE,
            cast(dict[str, Any], ctx.metadata)["bash"],  # Container name
            "/usr/bin/bash", "-c", command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,  # fmt: skip
        )

    def _terminate() -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()

    with ExitStack() as stack:
        stack.callback(_terminate)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), max(0, min(timeout, 300)))
        except TimeoutError:
            raise ToolExecutionError("command execution timed out")

    sections: list[str] = []
    if proc.returncode != 0:
        sections.append(f"Error: exit code: {proc.returncode}")
    if stderr:
        sections.append(f"{"[stderr]\n" if stdout else ""}{stderr.decode("utf-8").rstrip()}")
    if stdout:
        sections.append(f"{"[stdout]\n" if stderr else ""}{stdout.decode("utf-8").rstrip()}")

    return truncate_text_by_tokens(
        text="\n\n".join(sections),
        max_tokens=int(20_000),
        offset=None,
    )


bash = Tool(
    _bash,
    name="bash",
    max_retries=0,
    metadata={"close": _remove_podman_container},
)
