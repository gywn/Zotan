from __future__ import annotations

import dataclasses
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import tomli_w
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ModelRequest, ModelResponse, PartDeltaEvent, PartEndEvent, PartStartEvent, RunContext, SystemPromptPart, ToolCallPart, ToolReturnPart

from ..functional import cast_list
from ..toml import remove_none
from ..types_ import AgentSession, MainRunContext, Stack, StackFrame, ToolExecution, set_pending


def _get_session_dir(workspace_dir: Path) -> Path:
    """Get the directory path where session files are stored."""
    return workspace_dir / ".zotan" / "session"


def get_new_session_file(workspace_dir: Path, agent_kind: str) -> Path:
    """Create a new session file path with a unique timestamp-based filename."""
    session_dir = _get_session_dir(workspace_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    return session_dir / f"{agent_kind}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_UTC.toml"


def get_latest_session_file(workspace_dir: Path, agent_kind: str) -> Path | None:
    """Find the most recent session file for a specific agent type."""
    if (
        (session_dir := _get_session_dir(workspace_dir)).exists()
        and (session_files := list(session_dir.glob(f"{agent_kind}_*.toml")))
    ):  # fmt: skip
        return max(session_files, key=lambda f: f.name)
    else:
        return None


def get_resumable_stack(stack: Stack, allow_pending: bool = True) -> Stack | None:
    """Resumable stacks are subsets of UI-presentable stacks; resumable stacks can be persisted on hard disks."""
    stack_: list[StackFrame] = []

    for frame in reversed(stack):
        if isinstance(frame, ToolExecution):
            if stack_:
                stack_.clear()
            if all(isinstance(p, ToolReturnPart) or p.exec_order != 0 for p in frame.parts):
                # Tool execution state must be completed or resumable (i.e. exec_order != 0)
                if allow_pending:
                    frame = set_pending(frame, True)
                    stack_.append(frame)
                else:
                    stack_.clear()
        elif isinstance(frame, ModelRequest):
            if (
                stack_
                # Discard system prompts without following user prompts
                or not any(isinstance(part, SystemPromptPart) for part in frame.parts)
            ):
                if not stack_:
                    if allow_pending:
                        frame = set_pending(frame, True)
                        stack_.append(frame)
                else:
                    stack_.append(frame)
        elif isinstance(frame, (PartStartEvent, PartDeltaEvent, PartEndEvent)):
            # No incomplete model responses
            stack_.clear()
        else:  # ModelResponse
            if stack_ and (
                isinstance(stack_[-1], ToolExecution)
                and (
                    # Drop tool executions whose IDs do not match those of the tool calling
                    {p.tool_call_id for p in stack_[-1].parts}
                    != {p.tool_call_id for p in frame.parts if isinstance(p, ToolCallPart)}
                )
                # Drop the last model request if it does not contain tool returns or user instructions to tool calls
                or len(stack_) == 1
                and isinstance(stack_[-1], ModelRequest)
                and not any(isinstance(part, ToolCallPart) for part in frame.parts)
            ):
                stack_.clear()
            if (
                stack_
                # Discard model responses with tool calls but without a corresponding tool execution
                or not any(isinstance(part, ToolCallPart) for part in frame.parts)
            ):
                stack_.append(frame)

    if all(isinstance(frame, ModelRequest) for frame in stack_):
        stack_.clear()

    if stack_ and isinstance(stack_[-1], (ToolExecution, ModelResponse)):
        # Invalid stack
        stack_.clear()

    if not stack_:
        return None

    return list(reversed(stack_))


def load_session_file(session_file: Path) -> AgentSession | None:
    """Load an agent session from a TOML file."""
    try:
        session_obj = tomllib.load(open(session_file, "rb"))
        session = TypeAdapter(AgentSession).validate_python(session_obj)
        if (stack := get_resumable_stack(session.stack)) is None:
            return None
        session.stack = stack
        return session
    except (tomllib.TOMLDecodeError, ValidationError):
        return None


def save_session_file(session_file: Path, stack: Stack) -> None:
    """Save agent session messages to a TOML file."""
    session_file.write_text(
        tomli_w.dumps(
            remove_none(TypeAdapter(AgentSession).dump_python(AgentSession(stack=stack))),
            multiline_strings=True,
        ),
        encoding="utf-8",
    )


@dataclasses.dataclass
class SpinSession:
    do_load_session: bool = True
    session_file: Path | None = None

    @staticmethod
    def from_session_denial() -> SpinSession:
        return SpinSession(do_load_session=False)

    async def spin_once(
        self,
        ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> Stack:
        if ctx.deps.workspace_dir is None:
            return stack

        agent_kind = ctx.deps.agent_kind

        # Load session
        if (
            not stack
            # When a session's approval is denied, need to re-run with do_load_session=False
            and self.do_load_session
            and (latest_session_file := get_latest_session_file(ctx.deps.workspace_dir, agent_kind)) is not None
            and (agent_session := load_session_file(latest_session_file)) is not None
        ):
            return cast_list(agent_session.stack)

        # Save session
        if not (resumable_stack := get_resumable_stack(stack)):
            pass
        elif self.session_file is None:
            # Create initial session file
            self.session_file = get_new_session_file(ctx.deps.workspace_dir, agent_kind)
            save_session_file(self.session_file, stack)
        elif (session := load_session_file(self.session_file)) is None:
            # Corrupted session file
            save_session_file(self.session_file, stack)
        elif resumable_stack != session.stack:
            # Updated context
            save_session_file(self.session_file, stack)

        return stack
