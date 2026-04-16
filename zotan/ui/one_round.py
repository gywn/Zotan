from __future__ import annotations

import dataclasses
import functools
from asyncio import CancelledError
from collections import defaultdict
from contextlib import AsyncExitStack

import tomli_w
from pydantic_ai import ModelRequest, ModelResponse, PartDeltaEvent, PartEndEvent, PartStartEvent, RunContext, TextPart, ThinkingPart, ToolCallPart, ToolReturnPart, UserPromptPart

from ..spin.supervisor import get_supervisor_ctx, get_supervisor_tools
from ..spin.tool_exec import SpinToolExec, StopRun
from ..text import truncate_text_by_tokens
from ..toml import remove_none
from ..types_ import MainRunContext, Stack, ToolExecution, get_common_prefix_length, get_instructive


@dataclasses.dataclass
class SpinOneRound:
    stacks_cache: dict[str, Stack] = dataclasses.field(default_factory=lambda: defaultdict(list))  # Mapping from agent_kind to the execution stack

    def _render_user_prompt(self, ctx: RunContext[MainRunContext], part: UserPromptPart, is_instruction: bool) -> None:
        message: str = ""
        ctx_: RunContext[MainRunContext] | None = ctx
        while ctx_ is not None:
            if is_instruction:
                message = "Instruction › " + message
                is_instruction = False
            elif ctx_.deps.agent_kind == "supervisor":
                # The user of supervisor is human
                message = "You › " + message
            else:
                # The user of sub-agents is supervisor
                message = "Supervisor › " + message
            ctx_ = ctx_.deps.parent
        print(f"{message}{part.content}")

    def _render_thinking_header(self, _part: ThinkingPart) -> None:
        print("Thinking:")

    def _render_tool_call(self, part: ToolCallPart) -> None:
        print(f"Tool Call: {part.tool_name} ({part.tool_call_id})")
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
                    max_tokens=100,
                    offset=None,
                    truncation_indicator="...",
                )
            for arg_line in truncated_arg_str.splitlines():
                print(f"  {arg_line}")

    def _render_tool_return(self, part: ToolReturnPart) -> None:
        content = part.content
        if isinstance(content, str):
            if not content.startswith("Error: "):
                if content.strip():
                    print(f"Result:")
                    if part.tool_name == "delegate_task":
                        truncated_content = content.strip()
                    else:
                        truncated_content = truncate_text_by_tokens(
                            text=content,
                            max_tokens=100,
                            offset=None,
                            truncation_indicator="...",
                        )
                    content_lines = truncated_content.splitlines()
                    for line in content_lines:
                        print(f"{line}")
                else:
                    print("Result: (No output)")
            else:
                print(f"Error: {content[7:].rstrip()}")
        else:
            print(f"{content!r}")

    def _render_response(self, part: TextPart):
        print("Response:")
        print(part.content.strip())

    async def spin_once(
        self,
        ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> Stack:
        agent_kind = ctx.deps.agent_kind
        stack_cache = self.stacks_cache[agent_kind]
        cpl = get_common_prefix_length(stack_cache, stack)

        for i in range(cpl, len(stack)):
            frame = stack[i]
            # print(f"{frame}")
            if isinstance(frame, (ToolExecution, PartStartEvent, PartDeltaEvent, PartEndEvent)):
                pass

            elif isinstance(frame, ModelRequest):
                for part in frame.parts:
                    if isinstance(part, ToolReturnPart):
                        self._render_tool_return(part)
                    elif isinstance(part, UserPromptPart):
                        if (is_instruction := get_instructive(frame)) or agent_kind == "supervisor":
                            self._render_user_prompt(ctx, part, is_instruction)

            else:  # ModelResponse
                for part in frame.parts:
                    if isinstance(part, ThinkingPart):
                        self._render_thinking_header(part)
                        print(f"{part.content.strip()}")
                    elif isinstance(part, ToolCallPart):
                        self._render_tool_call(part)
                    elif isinstance(part, TextPart):
                        if agent_kind == "supervisor":
                            self._render_response(part)

        if (
            # Model response with final results
            isinstance(last_frame := stack[-1], ModelResponse)
            and not any(isinstance(part, ToolCallPart) for part in last_frame.parts)
        ):
            raise StopRun(stack)

        self.stacks_cache[agent_kind] = stack
        return stack


async def run_one_round(main_ctx: MainRunContext, user_prompt: str) -> None:
    try:
        ctx = get_supervisor_ctx(main_ctx)
        tools = get_supervisor_tools(main_ctx)
        stack: Stack = [
            ModelRequest([UserPromptPart(user_prompt)]),
        ]

        async with AsyncExitStack() as exit_stack:
            spin_tool_exec = await SpinToolExec.from_tools(ctx, tools)
            exit_stack.push_async_callback(functools.partial(spin_tool_exec.close, ctx))
            spin_one_round = SpinOneRound()

            ctx.deps = dataclasses.replace(
                ctx.deps,
                spin_ui=spin_one_round.spin_once,
            )

            while True:
                stack = await spin_tool_exec.spin_once(ctx, stack)
                stack = await spin_one_round.spin_once(ctx, stack)

    except (CancelledError, StopRun):
        pass
