from __future__ import annotations

import dataclasses
from typing import Sequence

from pydantic_ai import ModelResponse, RunContext, RunUsage
from pydantic_ai.tools import Tool, ToolFuncEither

from ..types_ import MainRunContext, Stack, ToolExecution, get_llm_model


def get_supervisor_ctx(main_ctx: MainRunContext) -> RunContext[MainRunContext]:
    return RunContext(
        deps=main_ctx,
        model=get_llm_model(main_ctx.config.get_llm_config("reasoning")),
        usage=RunUsage(),
    )


def get_supervisor_tools(main_ctx: MainRunContext) -> Sequence[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]]:
    return []


@dataclasses.dataclass
class SpinSupervisor:
    async def spin_once(
        self,
        _ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> Stack:
        if not stack:
            # Inject system prompts
            pass
        elif isinstance(stack[-1], (ToolExecution, ModelResponse)):
            # TODO: Handle compression
            pass

        return stack
