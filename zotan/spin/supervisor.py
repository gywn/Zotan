from __future__ import annotations

import dataclasses
from typing import Sequence, cast

from pydantic_ai import ModelRequest, ModelResponse, RunContext, RunUsage, ToolCallPart, UserPromptPart
from pydantic_ai.tools import Tool, ToolFuncEither

from ..context_manage import get_request_notes
from ..functional import cast_list
from ..tools.bash_tools import bash
from ..tools.delegate_tools import delegate_task
from ..tools.file_tools import edit_file, read_file, write_file
from ..types_ import MainRunContext, Stack, ToolExecution, get_instructive, get_llm_model


def get_supervisor_ctx(main_ctx: MainRunContext) -> RunContext[MainRunContext]:
    return RunContext(
        deps=main_ctx,
        model=get_llm_model(main_ctx.config.get_llm_config("reasoning")),
        usage=RunUsage(),
    )


def get_supervisor_tools(main_ctx: MainRunContext) -> Sequence[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]]:
    tools: list[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]] = [
        delegate_task,
    ]

    if main_ctx.workspace_dir is not None:
        tools += [
            read_file,
            edit_file,
            write_file,
            bash,
        ]

    return tools


INSTRUCTION_RELAY_SUB_AGENT = (
    "If you do not receive additional information from files or other agents, "
    "and you believe the response of the sub-agent is comprehensive and directly addresses the user's question, "
    "do not summarize or reiterate the sub-agent's response. "
    "Instead, simply continue with your task, the system will relay the sub-agent's response directly to the user."
)  # fmt: skip


@dataclasses.dataclass
class SpinSupervisor:
    async def spin_once(
        self,
        ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> Stack:
        if not stack:
            # Inject system prompts
            pass
        elif (
            stack
            # After the first user input
            and all(isinstance(frame, ModelRequest) for frame in stack)
            and not get_instructive(last_request := cast(ModelRequest, stack[-1]))
            and (
                user_prompt := "\n\n".join(
                    stripped
                    for part in last_request.parts
                    # User input
                    if isinstance(part, UserPromptPart) and (stripped := str(part.content).strip())
                )
            )
        ):
            # Inject contextual notes right after user prompts as LLM cannot stably follow instructions in system prompts
            if notes := await get_request_notes(ctx, user_prompt):
                stack = cast_list(stack) + [
                    ModelRequest(
                        [UserPromptPart(note) for note in notes],
                        metadata={"is_instruction": True},
                    ),
                ]
        elif (
            len(stack) >= 2
            # A sub-agent's response after a single delegate_task call
            and isinstance(last_response := stack[-2], ModelResponse)
            and len([part for part in last_response.parts if isinstance(part, ToolCallPart) and part.tool_name == "delegate_task"]) == 1
            and isinstance(stack[-1], ModelRequest)
        ):
            stack = cast_list(stack) + [
                ModelRequest(
                    [UserPromptPart(INSTRUCTION_RELAY_SUB_AGENT)],
                    metadata={"is_instruction": True},
                )
            ]
        elif isinstance(stack[-1], (ToolExecution, ModelResponse)):
            # TODO: Handle compression
            pass

        return stack
