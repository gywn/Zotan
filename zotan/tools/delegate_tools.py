import dataclasses
import functools
from contextlib import AsyncExitStack

from pydantic_ai import ModelRequest, ModelResponse, RunContext, RunUsage, TextPart, Tool, ToolCallPart, UserPromptPart
from pydantic_ai._utils import is_async_callable
from pydantic_ai.tools import ToolFuncEither

from ..context_manage import get_request_notes
from ..functional import cast_list
from ..spin.session import SpinSession
from ..spin.tool_exec import SpinToolExec, StopRun
from ..types_ import MainRunContext, Stack, ToolExecution, ToolExecutionError, get_llm_model
from .bash_tools import bash
from .file_tools import edit_file, read_file, write_file
from .http_tools import fetch_http
from .rich_file_tools import parse_rich_file
from .serper_tools import google_search


async def _delegate_task(
    ctx: RunContext[MainRunContext],
    task_description: str,
) -> str:
    """Delegate a complex task to a skilled sub-agent.

    This tool offloads complex tasks requiring long context windows to a sub-agent that
    can process independently. The sub-agent receives a fresh context and handles the
    task autonomously, returning results to the calling agent. After completing the
    task, the sub-agent deletes its context. Therefore, multi-round conversations with
    sub-agents are NOT possible.

    ## Your Role as Supervisor

    **RETAIN for yourself (reasoning and judgment):**
    - Determining what to delegate and in what order
    - Interpreting and synthesizing sub-agent results
    - Final answer formulation and decision-making
    - Evaluating whether sub-agent results need refinement

    **DELEGATE to sub-agents (execution and exploration):**
    - File exploration and information gathering
    - Code structure analysis and pattern identification
    - Running commands and executing defined steps
    - Multi-step research tasks with known procedures

    ## Context Limitation for Sub-Agents

    Sub-agents begin with a fresh context and cannot access your internal reasoning.
    When delegating tasks, always provide comprehensive context including:
    - Files examined and their relevance
    - Key decisions made and the reasoning behind them
    - Current hypotheses and how you reached them
    - Directions already explored that didn't work and why they failed

    ## Sub-Agent Capabilities

    Sub-agents can access:
    - **File Operations**: read_file, edit_file, write_file
    - **Shell Commands**: bash (with sudo, apt, pip access)
    - **Web Access**: fetch_http (download full web pages), google_search
    - **Rich File Parsing**: parse_rich_file (PDF, DOCX, OCR...)

    Args:
        task_description: Detailed description of what needs to be done.

    Returns:
        A response containing:
        - run_id: Session identifier for the sub-agent run
        - token_usage: Total tokens consumed by the sub-agent
        - The sub-agent's response text
    """
    tools: list[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]] = [
        fetch_http,
    ]

    if ctx.deps.workspace_dir is not None:
        tools += [
            read_file,
            edit_file,
            write_file,
            bash,
        ]

    if ctx.deps.config.serper_api_key is not None:
        tools.append(google_search)

    if (
        # Need file access
        ctx.deps.workspace_dir is not None
        and ctx.deps.config.llamacloud_api_key is not None
    ):
        tools.append(parse_rich_file)

    child_ctx = dataclasses.replace(
        ctx,
        deps=dataclasses.replace(
            ctx.deps,
            agent_kind=ctx.tool_call_id,
            parent=ctx,
        ),
        model=get_llm_model(ctx.deps.config.get_llm_config("reasoning")),
        usage=RunUsage(),
        metadata=None,
    )

    stack: Stack = []

    async def _spin_sub_agent_once(ctx: RunContext[MainRunContext], stack: Stack) -> Stack:
        if not stack:
            stack = cast_list(stack) + [
                ModelRequest([UserPromptPart(task_description)]),
            ]
            if notes := await get_request_notes(ctx, task_description):
                stack = cast_list(stack) + [
                    ModelRequest(
                        [UserPromptPart(note) for note in notes],
                        metadata={"is_instruction": True},
                    )
                ]
        elif isinstance(stack[-1], (ToolExecution, ModelResponse)):
            # TODO: Handle compression
            pass

        return stack

    async with AsyncExitStack() as exit_stack:
        spin_tool_exec = await SpinToolExec.from_tools(child_ctx, tools)
        exit_stack.push_async_callback(functools.partial(spin_tool_exec.close, child_ctx))
        spin_session = SpinSession()

        try:
            while True:
                stack = await spin_tool_exec.spin_once(child_ctx, stack)
                stack = await spin_session.spin_once(child_ctx, stack)
                stack = await _spin_sub_agent_once(child_ctx, stack)
                if (spin_ui := ctx.deps.spin_ui) is not None:
                    stack = await spin_ui(child_ctx, stack) if is_async_callable(spin_ui) else spin_ui(child_ctx, stack)
                if (
                    # Response with final results
                    stack
                    and isinstance(stack[-1], ModelResponse)
                    and not any(isinstance(part, ToolCallPart) for part in stack[-1].parts)
                ):
                    raise StopRun(stack)
        except StopRun as stop_run:
            stack = stop_run.stack
            if (
                # Response with final results
                stack
                and isinstance(stack[-1], ModelResponse)
                and not any(isinstance(part, ToolCallPart) for part in stack[-1].parts)
            ):
                output = ("\n".join(part.content for part in stack[-1].parts if isinstance(part, TextPart))).strip()
            else:
                raise ToolExecutionError("Unexpected error")

    return output


delegate_task = Tool(
    _delegate_task,
    name="delegate_task",
    max_retries=2,
)
