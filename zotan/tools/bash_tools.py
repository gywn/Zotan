"""Bash execution tools for Zotan agent."""

import asyncio
import contextlib
import html
from contextlib import ExitStack
from typing import Any, cast

from pydantic_ai import RunContext, Tool

from ..config import WORKING_MODE, WORKSPACE
from ..context_manage import process_text
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
        "zotan:rust",
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


# As models get more capable, some of what lives in the harness today will get absorbed into the model.
def _get_instruction_complex_bash_use(_ctx: RunContext[MainRunContext]) -> str:
    return f"""
<instruction>Evaluate whether this task should be delegated to a sub-agent rather than executed via raw Bash commands</instruction>
<rule>Respond with "APPROVED" to approve the command</rule>
<rule>When rejecting, instruct the user to delegate tasks to sub-agents</rule>
<rule>When in doubt or if intent is ambiguous, reject the command</rule>
<category name="Complex Multi-Step Tasks">
  <description>Tasks requiring multiple steps, extensive Bash usage, or exceeding basic shell capabilities</description>
  <example>Analyze the codebase structure</example>
  <example>Analyze requirements, implement solution, then test</example>
  <example>python -c "..."</example>
</category>
<category name="Large Output Generation">
  <description>Commands producing excessive output or performing bulk operations</description>
  <example>cat, grep, find, xargs, curl, head, tail, wc...</example>
</category>
<category name="Environment Modification">
  <description>Commands installing dependencies or modifying system packages</description>
  <example>apt, apt-get, pip, npm...</example>
</category>
<category name="Long-Running Tasks">
  <description>Commands running for extended periods or consuming significant CPU resources</description>
  <example>g++, gcc, clang, make, cmake, pyright, mypy, eslint, tsc, java, javac, go build, rustc, cargo build, python -m pytest, node --test...</example>
</category>
<example>
  <name>Simple File Renaming</name>
  <user>
    Task: Read untitled.md, summarize its content, and rename it to a better name.
    Command: mv untitled.md market_research.md
    Intent: Rename information summary file
  </user>
  <answer>APPROVED</answer>
</example>
<example>
  <name>APT Package Installation</name>
  <user>
    Task: Fix compilation errors in my C++ project.
    Command: sudo apt install build-essential
    Intent: Install GCC toolchain
  </user>
  <answer>Use a sub-agent for package installation</answer>
</example>
<example>
  <name>Code Analysis via Directory Traversal</name>
  <user>
    Task: Understand this repository's structure.
    Command: find /workspace -type f -name '*.ts'
    Intent: List all TypeScript files in the workspace
  </user>
  <answer>Use a sub-agent for codebase analysis</answer>
</example>
<example>
  <name>Syntax Verification via Python</name>
  <user>
    Task: Download all new photos from my website.
    Command: python -c "import ast; ast.parse(open('download_photos.py').read())"
    Intent: Verify Python syntax is correct before running the script
  </user>
  <answer>Use a sub-agent for linting and type checking</answer>
</example>
<example>
  <name>C++ Compilation</name>
  <user>
    Task: Fix compilation errors in my C++ project.
    Command: g++ main.cpp -o main
    Intent: Compile the codebase
  </user>
  <answer>Use a sub-agent for resource-intensive operations</answer>
</example>
""".strip()


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

    user_prompt = html.escape(f"Task:{ctx.prompt}\nIntent: {intent}\nCommand: {command}".strip())
    complex_bash_note = await process_text(ctx.deps, _get_instruction_complex_bash_use(ctx), user_prompt)
    if ctx.deps.agent_kind == "supervisor" and complex_bash_note.strip() != "APPROVED":
        raise ToolExecutionError(complex_bash_note.strip())

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
