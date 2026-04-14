from __future__ import annotations

import dataclasses
from typing import Sequence

from pydantic_ai import ModelResponse, RunContext, RunUsage
from pydantic_ai.tools import Tool, ToolFuncEither

from ..tools.bash_tools import bash
from ..tools.file_tools import edit_file, read_file, write_file
from ..tools.http_tools import fetch_http
from ..tools.rich_file_tools import parse_rich_file
from ..tools.serper_tools import get_current_date, google_search
from ..types_ import MainRunContext, Stack, ToolExecution, get_llm_model


def get_supervisor_ctx(main_ctx: MainRunContext) -> RunContext[MainRunContext]:
    return RunContext(
        deps=main_ctx,
        model=get_llm_model(main_ctx.config.get_llm_config("reasoning")),
        usage=RunUsage(),
    )


def get_supervisor_tools(main_ctx: MainRunContext) -> Sequence[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]]:
    tools: list[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]] = [
        fetch_http,
    ]

    if main_ctx.workspace_dir is not None:
        tools += [
            read_file,
            edit_file,
            write_file,
            bash,
        ]

    if main_ctx.config.serper_api_key:
        tools += [
            get_current_date,
            google_search,
        ]

    if (
        # Need file access
        main_ctx.workspace_dir is not None
        and main_ctx.config.llamacloud_api_key
    ):
        tools.append(parse_rich_file)

    return tools


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
