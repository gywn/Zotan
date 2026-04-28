from __future__ import annotations

import dataclasses
from typing import AsyncGenerator, AsyncIterator, Awaitable, Callable, Sequence, TypeAlias, cast

from pydantic import ValidationError
from pydantic_ai import FunctionToolset, ModelMessage, ModelRequest, ModelResponse, PartDeltaEvent, PartEndEvent, PartStartEvent, RunContext, RunUsage, Tool, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai._utils import is_async_callable
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolFuncEither
from pydantic_ai.toolsets.function import FunctionToolsetTool

from ..functional import cast_list, maybe_next
from ..types_ import MainRunContext, Stack, StackFrame, ToolExecution, ToolExecutionError, ToolExecutionPart, get_instructive, get_pending


def validate_runnable_stack(stack: Stack) -> bool:
    """Runnable stacks are subsets of UI-presentable stacks; Runnable stacks can be processed by agent.run()"""
    next_frame: StackFrame | None = None

    for frame in reversed(stack):
        if isinstance(frame, ToolExecution):
            if next_frame is not None:
                return False
        elif isinstance(frame, ModelRequest):
            if not isinstance(next_frame, (ModelRequest, ModelResponse)):
                return False
        elif isinstance(frame, (PartStartEvent, PartDeltaEvent, PartEndEvent)):
            return False
        else:  # ModelResponse
            if (
                isinstance(next_frame, ToolExecution)
                and (
                    # Tool execution IDs must match those of the tool calling
                    {p.tool_call_id for p in next_frame.parts}
                    != {p.tool_call_id for p in frame.parts if isinstance(p, ToolCallPart)}
                )
                or not isinstance(next_frame, (ModelRequest, ModelResponse))
            ):
                return False
        next_frame = frame

    return not (
        # Empty stack
        next_frame is None
        or isinstance(next_frame, (ToolExecution, ModelResponse))
    )


@dataclasses.dataclass
class StopRun(Exception):
    stack: Stack = ()


ToolCloseCallback: TypeAlias = Callable[[RunContext[MainRunContext]], Awaitable[None]] | Callable[[RunContext[MainRunContext]], None]


@dataclasses.dataclass
class SpinToolExec:
    func_tools: dict[str, FunctionToolsetTool[MainRunContext]]
    tool_close_callbacks: Sequence[ToolCloseCallback]
    iter: AsyncIterator[Stack] | None = None

    @staticmethod
    async def from_tools(
        ctx: RunContext[MainRunContext],
        tools: Sequence[Tool[MainRunContext] | ToolFuncEither[MainRunContext, ...]] = (),
    ) -> SpinToolExec:
        toolset = FunctionToolset(tools)
        return SpinToolExec(
            func_tools=cast(dict[str, FunctionToolsetTool[MainRunContext]], await toolset.get_tools(ctx)),
            tool_close_callbacks=[cast(ToolCloseCallback, cb) for tool in toolset.tools.values() if tool.metadata is not None and callable(cb := tool.metadata.get("close"))],
        )

    async def _get_generator(
        self,
        ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> AsyncGenerator[Stack]:
        if not stack:
            yield stack

        elif isinstance(stack[-1], ToolExecution):
            if get_pending(stack[-1]):
                yield stack
            else:
                assert isinstance(stack[-2], ModelResponse)
                tool_calls = {p.tool_call_id: p for p in stack[-2].parts if isinstance(p, ToolCallPart)}
                tool_execs = list(stack[-1].parts)

                # pyright has bugs and cannot use lambda
                def _get_exec_order(i: int) -> int:
                    return p.exec_order if isinstance(p := tool_execs[i], ToolExecutionPart) else 0

                for i in sorted(range(len(tool_execs)), key=_get_exec_order):
                    if isinstance(tool_exec := tool_execs[i], ToolExecutionPart):
                        tool = self.func_tools[tool_name := (tool_call := tool_calls[tool_call_id := tool_exec.tool_call_id]).tool_name]
                        assert isinstance(tool_call.args, str)
                        try:
                            validated_args = tool.args_validator.validate_json(tool_call.args, context=ctx.validation_context)
                        except ValidationError:
                            tool_result = ToolExecutionError(f"Invalid arguments for {tool_name}")
                        else:
                            tool_call_ctx = dataclasses.replace(
                                ctx,
                                usage=(
                                    RunUsage(
                                        input_tokens=last_response.usage.input_tokens,
                                        output_tokens=last_response.usage.output_tokens,
                                        cache_read_tokens=last_response.usage.cache_read_tokens,
                                    )
                                    if (last_response := maybe_next(frame for frame in reversed(stack) if isinstance(frame, ModelResponse))) is not None
                                    else RunUsage()
                                ),
                                tool_call_id=tool_call_id,
                            )

                            try:
                                tool_result = await tool.call_func(validated_args, tool_call_ctx)
                            except ToolExecutionError as e:
                                tool_result = e

                        tool_execs[i] = ToolReturnPart(
                            tool_name=tool_name,
                            content=f"Error: {tool_result.message}" if isinstance(tool_result, ToolExecutionError) else tool_result,
                            tool_call_id=tool_call_id,
                        )
                        yield cast_list(stack[:-1]) + [dataclasses.replace(stack[-1], parts=tool_execs[:])]

                yield cast_list(stack[:-1]) + [ModelRequest(cast(Sequence[ToolReturnPart], tool_execs), metadata={"is_instruction": True})]

        elif isinstance(stack[-1], ModelRequest):
            if get_pending(stack[-1]):
                yield stack
            else:
                # The model request does not require user approval
                async with ctx.model.request_stream(
                    messages=cast(list[ModelMessage], stack),
                    model_settings=None,
                    model_request_parameters=ModelRequestParameters(
                        function_tools=[tool.tool_def for tool in self.func_tools.values()],
                    ),
                ) as stream:
                    stream_stack = stack
                    async for event in stream:
                        if isinstance(event, (PartStartEvent, PartDeltaEvent, PartEndEvent)):
                            stream_stack = cast_list(stream_stack) + [event]
                            yield stream_stack
                    response = stream.get()

                yield cast_list(stack) + [response]

        elif isinstance(stack[-1], (PartStartEvent, PartDeltaEvent, PartEndEvent)):
            yield stack

        else:  # ModelResponse
            if tool_calls := {p.tool_call_id: p for p in stack[-1].parts if isinstance(p, ToolCallPart)}:
                try:
                    user_prompt = next(
                        "\n\n".join(str(part.content) for part in frame.parts if isinstance(part, UserPromptPart))
                        # Find out the last user prompt
                        for frame in reversed(stack)
                        if isinstance(frame, ModelRequest) and not get_instructive(frame)
                    )
                except StopIteration:
                    user_prompt = ""
                ctx.prompt = user_prompt
                tool_execs = [
                    (
                        ToolExecutionPart(
                            tool_call_id=tool_call_id,
                            exec_order=func_tool.max_retries,
                        )
                        if (func_tool := self.func_tools.get(tool_name := tool_calls[tool_call_id].tool_name)) is not None
                        else ToolReturnPart(
                            tool_name=tool_name,
                            content=f"Error: {tool_name} is not available as a tool",
                            tool_call_id=tool_call_id,
                        )
                    )
                    for tool_call_id in tool_calls.keys()
                ]
                yield cast_list(stack) + [ToolExecution(parts=tool_execs)]
            else:
                yield stack

    async def spin_once(
        self,
        ctx: RunContext[MainRunContext],
        stack: Stack,
    ) -> Stack:
        if self.iter is not None:
            try:
                return await anext(self.iter)
            except StopAsyncIteration:
                self.iter = None
        if self.iter is None:
            self.iter = self._get_generator(ctx, stack)
        return await anext(self.iter)

    async def close(self, ctx: RunContext[MainRunContext]) -> None:
        for cb in self.tool_close_callbacks:
            await cb(ctx) if is_async_callable(cb) else cb(ctx)
