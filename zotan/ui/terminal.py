from __future__ import annotations

import dataclasses
import functools
import getpass
import itertools
from asyncio import CancelledError
from contextlib import AsyncExitStack
from pathlib import Path

import tomli_w
from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import get_app_session
from prompt_toolkit.enums import EditingMode
from prompt_toolkit.formatted_text import FormattedText, OneStyleAndTextTuple, to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.shortcuts import set_title
from prompt_toolkit.styles import Style
from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ModelRequest, ModelResponse, PartDeltaEvent, PartEndEvent, PartStartEvent, RunContext, SystemPromptPart, TextPart, ThinkingPart, ThinkingPartDelta, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.usage import UsageBase

from ..functional import cast_list
from ..spin.session import SpinSession, get_resumable_stack
from ..spin.supervisor import SpinSupervisor, get_supervisor_ctx, get_supervisor_tools
from ..spin.tool_exec import SpinToolExec, StopRun
from ..text import print_markdown, truncate_text_by_tokens
from ..toml import remove_none
from ..types_ import MainRunContext, Stack, ToolExecution, ToolExecutionError, ToolExecutionPart, get_common_prefix_length, get_instructive, get_pending, set_pending


# ANSI color codes
class Colors:
    """Terminal color definitions"""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Foreground colors
    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright colors
    BRIGHT_BLACK = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    # Background colors
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


@dataclasses.dataclass
class SessionDenial(Exception):
    pass


@dataclasses.dataclass
class SpinTerminal:
    prompt_session: PromptSession[str]

    @staticmethod
    def from_ctx(
        ctx: RunContext[MainRunContext],
    ) -> SpinTerminal:
        history_file = get_history_file(ctx.deps.workspace_dir)
        if history_file is not None and not history_file.exists():
            history_file.parent.mkdir(parents=True, exist_ok=True)
        if ctx.deps.config.editing_mode == "vi":
            editing_mode_ = EditingMode.VI
        else:  # Default
            editing_mode_ = EditingMode.EMACS
        prompt_style = Style(
            [
                ("prompt", "ansigreen bold"),  # Green and bold
                ("instruction", "ansiyellow bold"),  # Green and bold
                ("supervisor", "ansiblue bold"),  # Green and bold
                ("placeholder", "dim"),
            ]
        )
        prompt_session = PromptSession[str](
            [("class:prompt", "You › ")],
            multiline=True,
            editing_mode=editing_mode_,
            enable_history_search=True,
            style=prompt_style,
            history=FileHistory(history_file) if history_file is not None else None,
            prompt_continuation=[("class:prompt", "..... ")],
            placeholder=None,
        )

        return SpinTerminal(
            prompt_session=prompt_session,
        )

    def _render_user_prompt(self, ctx: RunContext[MainRunContext], part: UserPromptPart, is_instruction: bool) -> None:
        print()
        message: list[OneStyleAndTextTuple] = []
        ctx_: RunContext[MainRunContext] | None = ctx
        if is_instruction:
            user_prompt = [("class:placeholder", str(part.content))]
        else:
            user_prompt = [("", str(part.content))]
        while ctx_ is not None:
            if is_instruction:
                message = to_formatted_text([("class:instruction", "Instruction › ")]) + message
                is_instruction = False
            elif ctx_.deps.agent_kind == "supervisor":
                # The user of supervisor is human
                message = to_formatted_text(self.prompt_session.message) + message
            else:
                # The user of sub-agents is supervisor
                message = to_formatted_text([("class:supervisor", "Supervisor › ")]) + message
            ctx_ = ctx_.deps.parent
        print_formatted_text(
            FormattedText(message + user_prompt),
            style=self.prompt_session.style,
        )

    def _render_thinking_header(self, _part: ThinkingPart) -> None:
        print(f"\n{Colors.BOLD}{Colors.MAGENTA}Thinking:{Colors.RESET}")

    def _render_tool_call_header(self, part: ToolCallPart) -> None:
        print(f"\n{Colors.BOLD}{Colors.YELLOW}Tool Call:{Colors.RESET} {Colors.BOLD}{Colors.CYAN}{part.tool_name}{Colors.RESET} ({Colors.CYAN}{part.tool_call_id}{Colors.RESET})")

    def _render_tool_call_body(self, part: ToolCallPart) -> None:
        for arg_name, arg_value in remove_none(part.args_as_dict()).items():
            arg_str = tomli_w.dumps(
                {arg_name: arg_value},
                multiline_strings=True,
                indent=2,
            )
            if part.tool_name == "delegate_task":
                truncated_arg_str = arg_str
            else:
                truncated_arg_str = truncate_text_by_tokens(
                    text=arg_str,
                    max_tokens=200,
                    offset=None,
                    truncation_indicator="...",
                )
            for arg_line in truncated_arg_str.splitlines():
                print(f"  {Colors.DIM}{arg_line}{Colors.RESET}")

    def _render_tool_return(self, part: ToolReturnPart) -> None:
        content = part.content
        if isinstance(content, str):
            if not content.startswith("Error: "):
                if content.strip():
                    print(f"\n{Colors.BOLD}{Colors.GREEN}Result: {Colors.RESET}")
                    if part.tool_name == "delegate_task":
                        print_markdown(content.strip())
                    else:
                        truncated_content = truncate_text_by_tokens(
                            text=content,
                            max_tokens=200,
                            offset=None,
                            truncation_indicator="...",
                        )
                        content_lines = truncated_content.splitlines()
                        for line in content_lines:
                            print(f"{Colors.DIM}{line}{Colors.RESET}")
                else:
                    print(f"\n{Colors.BOLD}{Colors.GREEN}Result: {Colors.RESET}{Colors.DIM}(No output){Colors.RESET}")
            else:
                print(f"\n{Colors.BOLD}{Colors.RED}Error: {Colors.RESET}{Colors.RED}{content[7:].rstrip()}{Colors.RESET}")
        else:
            print(f"\n{Colors.DIM}{content!r}{Colors.RESET}")

    def _render_response_header(self, _part: TextPart):
        print(f"\n{Colors.BOLD}{Colors.BLUE}Response:{Colors.RESET}")

    def _render_response_body(self, part: TextPart):
        print_markdown(part.content.strip())

    def _render_progress_bar(self, progress: float, total: float) -> str:
        """Render a single progress bar"""
        # Block characters for fractional fills (index = number of eighths filled)
        block_chars = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]
        n_block = 8

        progress_bar = ""
        for i in range(n_block):
            block_start = i / n_block * total
            block_end = (i + 1) / n_block * total
            if block_end < progress:
                progress_bar += block_chars[8]
            elif block_start <= progress:
                progress_bar += block_chars[int(round((progress / total * n_block - i) * 8))]
            else:
                progress_bar += " "

        return f"\033[2;42;7m{progress_bar}{Colors.RESET}"

    def _render_token_usage(self, ctx: RunContext[MainRunContext], usage: UsageBase) -> None:
        # Collect (usage, max_context) pairs from current and all parent contexts
        token_stack: list[tuple[int, int]] = [(usage.total_tokens, 200_000)]
        parent = ctx.deps.parent
        while parent is not None:
            token_stack.append((parent.usage.total_tokens, 200_000))
            parent = parent.deps.parent

        # Render progress bars for each context level
        progress_bars: list[str] = []
        for total_tokens, max_context in reversed(token_stack):
            progress_bar = self._render_progress_bar(total_tokens, max_context)
            progress_bars.append(progress_bar)

        progress_bar_str = f"{Colors.DIM} › {Colors.RESET}".join(progress_bars)
        print(f"\n{Colors.DIM}🪙 Context {progress_bar_str}{Colors.RESET}")

    def _render_delimiter(self) -> None:
        print(f"\n{Colors.DIM}{'─' * 60}{Colors.RESET}")

    async def _approve_session(self) -> None:
        user_input = ""
        while not user_input:
            try:
                user_input = await self.prompt_session.prompt_async()
            except (KeyboardInterrupt, EOFError):
                if (current_buffer := self.prompt_session.app.current_buffer) and current_buffer.text.strip():
                    # Text exists - abort input but keep text visible and start new prompt
                    current_buffer.reset()
                    continue
                else:
                    print(f"\n{Colors.YELLOW}Approval denied.{Colors.RESET}")
                    raise SessionDenial()

            user_input = user_input.strip()
            if user_input.lower() in ("y", "yes"):
                break
            elif user_input:
                print(f"\n{Colors.YELLOW}Approval denied.{Colors.RESET}")
                raise SessionDenial()

    async def spin_once(
        self,
        ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> Stack:
        if ctx.metadata is None:
            ctx.metadata = dict()
        try:
            stack_cache: Stack = TypeAdapter(Stack).validate_python(ctx.metadata.get("spin_terminal"), strict=True)
        except ValidationError:
            stack_cache = []
            ctx.metadata["spin_terminal"] = stack_cache

        cpl = get_common_prefix_length(stack_cache, stack)
        agent_kind = ctx.deps.agent_kind

        for i in range(cpl, len(stack)):
            frame = stack[i]
            # print(f"\n{Colors.DIM}{frame}{Colors.RESET}")
            if isinstance(frame, ToolExecution):
                for j in range(len(frame.parts)):
                    part = frame.parts[j]
                    if (
                        isinstance(part, ToolReturnPart)
                        and part.metadata is None  # No instructions
                        and not (
                            # Tool return has been rendered
                            i < len(stack_cache)
                            and isinstance(tool_exec_cache := stack_cache[i], ToolExecution)
                            and j < len(tool_exec_cache.parts)
                            and isinstance(tool_exec_cache.parts[j], ToolReturnPart)
                        )
                    ):
                        if isinstance(part.content, ToolExecutionError):
                            # Deprecated: It appears that the LLM must receive a tool return corresponding to the tool call
                            self._render_user_prompt(ctx, UserPromptPart(part.content.message), True)
                        else:
                            self._render_tool_return(part)
                    elif isinstance(part, ToolCallPart):
                        set_title(f"🚀{part.tool_name}")
                        get_app_session().output.flush()

            elif isinstance(frame, ModelRequest):
                if not (
                    # This frame replaces a tool execution
                    i == cpl
                    and stack_cache[cpl:]
                    and all(isinstance(frame, ToolExecution) for frame in stack_cache[cpl:])
                ):
                    # Not overwriting tool returns rendered by ToolExecution
                    for part in frame.parts:
                        if isinstance(part, ToolReturnPart):
                            self._render_tool_return(part)
                        elif isinstance(part, UserPromptPart):
                            if (is_instruction := get_instructive(frame)) or agent_kind == "supervisor":
                                self._render_user_prompt(ctx, part, is_instruction)

                if not get_pending(frame):
                    set_title("🤔Thinking…")
                    get_app_session().output.flush()

            elif isinstance(frame, PartStartEvent):
                if isinstance(frame.part, ThinkingPart):
                    self._render_thinking_header(frame.part)
                    print(f"{Colors.DIM}{frame.part.content}{Colors.RESET}", end="", flush=True)
                elif isinstance(frame.part, ToolCallPart):
                    self._render_tool_call_header(frame.part)
                elif isinstance(frame.part, TextPart):
                    if agent_kind == "supervisor":
                        self._render_response_header(frame.part)

            elif isinstance(frame, PartDeltaEvent):
                if isinstance(frame.delta, ThinkingPartDelta) and frame.delta.content_delta is not None:
                    print(f"{Colors.DIM}{frame.delta.content_delta}{Colors.RESET}", end="", flush=True)

            elif isinstance(frame, PartEndEvent):
                if isinstance(frame.part, ThinkingPart):
                    if frame.part.content.rstrip() == frame.part.content:
                        print()
                elif isinstance(frame.part, ToolCallPart):
                    self._render_tool_call_body(frame.part)
                elif isinstance(frame.part, TextPart):
                    if agent_kind == "supervisor":
                        self._render_response_body(frame.part)

            else:  # ModelResponse
                if not (
                    # Not overwriting response parts rendered by stream events
                    stack_cache[cpl:]
                    and all(isinstance(frame, (PartStartEvent, PartDeltaEvent, PartEndEvent)) for frame in stack_cache[cpl:])
                ):
                    for part in frame.parts:
                        if isinstance(part, ThinkingPart):
                            self._render_thinking_header(part)
                            print(f"{Colors.DIM}{part.content.strip()}{Colors.RESET}")
                        elif isinstance(part, ToolCallPart):
                            self._render_tool_call_header(part)
                            self._render_tool_call_body(part)
                        elif isinstance(part, TextPart):
                            if agent_kind == "supervisor":
                                self._render_response_header(part)
                                self._render_response_body(part)

                self._render_token_usage(ctx, frame.usage)

                if not any(isinstance(part, ToolCallPart) for part in frame.parts):
                    # Final results
                    self._render_delimiter()
                    set_title(f"🪙{frame.usage.total_tokens}")
                    get_app_session().output.flush()

        if len(stack) == cpl > len(stack_cache):
            # This is a roll-back
            self._render_delimiter()

        if (
            # The last frame contain pending tool results
            stack
            and isinstance(last_frame := stack[-1], ModelRequest)
            and get_pending(last_frame)
        ):
            if (
                len(stack) > 1
                and isinstance(stack[-2], ModelResponse)
                # The last frame contains tool returns or user instructions after tool calls
                and any(isinstance(part, ToolCallPart) for part in stack[-2].parts)
            ):
                tool_call_ids = [f"{Colors.CYAN}{part.tool_call_id}{Colors.RESET}" for part in stack[-2].parts if isinstance(part, ToolCallPart)]
                print(f"\n{Colors.YELLOW}Do you approve sending tool results or user instructions of {Colors.RESET}{f"{Colors.YELLOW}, {Colors.RESET}".join(tool_call_ids)}{Colors.YELLOW}? (yes/NO){Colors.RESET}\n")

                await self._approve_session()  # raise SessionDenied

                last_frame = set_pending(last_frame, False)
                stack = cast_list(stack[:-1]) + [last_frame]

            else:
                print(f"\n{Colors.YELLOW}Do you approve sending the user prompt? (yes/NO){Colors.RESET}\n")

                await self._approve_session()  # raise SessionDenied

                last_frame = set_pending(last_frame, False)
                stack = cast_list(stack[:-1]) + [last_frame]

        elif (
            # The last frame contain pending tool executions
            stack
            and isinstance(last_frame := stack[-1], ToolExecution)
            and get_pending(last_frame)
        ):
            tool_call_ids = [f"{Colors.CYAN}{part.tool_call_id}{Colors.RESET}" for part in last_frame.parts if isinstance(part, ToolExecutionPart)]
            print(f"\n{Colors.YELLOW}Do you approve the resumption of tool executions {Colors.RESET}{f"{Colors.YELLOW}, {Colors.RESET}".join(tool_call_ids)}{Colors.YELLOW}? (yes/NO){Colors.RESET}\n")

            await self._approve_session()  # raise SessionDenied

            last_frame = set_pending(last_frame, False)
            stack = cast_list(stack[:-1]) + [last_frame]

        elif (
            # No system prompts
            not stack
            # System prompts
            or isinstance(last_frame := stack[-1], ModelRequest)
            and any(isinstance(part, SystemPromptPart) for part in last_frame.parts)
            # Model response with final results
            or isinstance(last_frame := stack[-1], ModelResponse)
            and not any(isinstance(part, ToolCallPart) for part in last_frame.parts)
        ):
            if agent_kind != "supervisor":
                # Sub-agents run for only one round
                raise StopRun(stack)

            print()
            user_input = ""
            while not user_input:
                try:
                    user_input = await self.prompt_session.prompt_async()
                except (KeyboardInterrupt, EOFError):
                    if (current_buffer := self.prompt_session.app.current_buffer) and current_buffer.text.strip():
                        # Text exists - abort input but keep text visible and start new prompt
                        current_buffer.reset()
                        continue
                    else:
                        raise StopRun(stack)

                user_input = user_input.strip()
                if user_input == "/clear":
                    raise SessionDenial()

            set_title("🤔Thinking…")
            get_app_session().output.flush()

            stack = cast_list(stack) + [ModelRequest([UserPromptPart(user_input)])]

        ctx.metadata["spin_terminal"] = stack
        return stack


async def run_supervisor(main_ctx: MainRunContext) -> None:
    try:
        print("Welcome to the Zotan REPL. Press Ctrl+C or Ctrl+D to exit.")

        ctx = get_supervisor_ctx(main_ctx)
        tools = get_supervisor_tools(main_ctx)
        stack: Stack = []

        async with AsyncExitStack() as exit_stack:
            spin_tool_exec = await SpinToolExec.from_tools(ctx, tools)
            exit_stack.push_async_callback(functools.partial(spin_tool_exec.close, ctx))
            spin_session = SpinSession()
            spin_supervisor = SpinSupervisor()
            spin_terminal = SpinTerminal.from_ctx(ctx)

            ctx.deps = dataclasses.replace(
                ctx.deps,
                spin_ui=spin_terminal.spin_once,
            )

            while True:
                try:
                    stack = await spin_supervisor.spin_once(ctx, stack)
                    stack = await spin_tool_exec.spin_once(ctx, stack)
                    stack = await spin_session.spin_once(ctx, stack)
                    stack = await spin_supervisor.spin_once(ctx, stack)
                    stack = await spin_terminal.spin_once(ctx, stack)
                except (SessionDenial, CancelledError):
                    if (stack_ := get_resumable_stack(stack, allow_pending=False) or []) != stack:
                        # Roll back pending resumable stacks to non-pending resumable stacks
                        stack = stack_
                    else:
                        # Roll back the non-pending resumable stacks to empty stacks
                        stack = []
                    if ctx.metadata is None:
                        ctx.metadata = dict()
                    ctx.metadata["spin_terminal"] = stack
                    if not stack:
                        print(f"\n{Colors.BOLD}Session cleared{Colors.RESET}")
                    else:
                        print(f"\n{Colors.BOLD}Session rolled back{Colors.RESET}")
                    spin_session = SpinSession.from_session_denial()

    except (CancelledError, StopRun):
        print("\nExiting...")


def get_history_file(workspace_dir: Path | None) -> Path | None:
    """Get the path to the history file for storing conversation history."""
    if workspace_dir is not None:
        workspace_dir = workspace_dir.expanduser().absolute()
        for project_dir in itertools.chain([workspace_dir], workspace_dir.parents):
            if project_dir.owner() != getpass.getuser() or project_dir == Path.home():
                break
            history_path = project_dir / ".zotan" / "history"
            if history_path.exists():
                return history_path
        return workspace_dir / ".zotan" / "history"
    return None
