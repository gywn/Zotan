from __future__ import annotations

import dataclasses
import html
from typing import Sequence, cast

from pydantic_ai import ModelRequest, ModelResponse, RunContext, RunUsage, SystemPromptPart, ToolCallPart, UserPromptPart

from .config import WORKSPACE
from .spin.tool_exec import SpinToolExec, StopRun
from .tools.serper_tools import get_current_date
from .types_ import MainRunContext, Stack, get_llm_model


async def process_text(
    main_ctx: MainRunContext,
    system_prompt: str,
    user_prompt: str,
) -> str:
    ctx = RunContext(
        deps=dataclasses.replace(
            main_ctx,
            agent_kind="process_text",
            parent=None,
        ),
        model=get_llm_model(main_ctx.config.get_llm_config("text_processing"), thinking=False),
        usage=RunUsage(),
        metadata=None,
    )
    stack: Stack = [
        ModelRequest([SystemPromptPart(system_prompt)]),
        ModelRequest([UserPromptPart(user_prompt)]),
    ]

    spin_tool_exec = await SpinToolExec.from_tools(ctx, [])

    try:
        while True:
            stack = await spin_tool_exec.spin_once(ctx, stack)
            if (
                # Model response with final results
                isinstance(last_frame := stack[-1], ModelResponse)
                and not any(isinstance(part, ToolCallPart) for part in last_frame.parts)
            ):
                raise StopRun(stack)
    except StopRun:
        pass

    return cast(ModelResponse, stack[-1]).text or ""


# As models get more capable, some of what lives in the harness today will get absorbed into the model.
def _get_instruction_contextual_info(ctx: RunContext[MainRunContext]) -> str:
    date = get_current_date(ctx)
    cwd = WORKSPACE
    return f"""
<instruction>Generate context notes based on the content of the user prompt for any contextual information that may affect how the user prompt is executed or results are interpreted.</instruction>
<rule>NEVER respond with notes that can be directly inferred from the task description alone</rule>
<rule>If no notes apply, respond exactly: NO_NOTES</rule>
<rule>Do NOT summarize the task or explain your reasoning</rule>
<data>
  <description>Known Contextual Information</description>
  <item>
    <expression>Current Date: {date}</expression>
    <condition>If the task involves recency, trends, updates, or specific timeframes</condition>
  </item>
  <item>
    <expression>Current Working Directory: {cwd}</expression>
    <condition>If answers may be saved locally, files may be created, or operations occur in the current environment</condition>
  </item>
</data>
<example>
  <name>Time-Sensitive Task</name>
  <user>Find last week's trending news.</user>
  <answer>Current Date: {date}\nCurrent Working Directory: {cwd}</answer>
</example>
<example>
  <name>File Operations</name>
  <user>Save search results to a CSV file.</user>
  <answer>Current Working Directory: {cwd}</answer>
</example>
<example>
 <name>Pure Static Informational Query</name>
 <user>What is the capital of France?</user>
 <answer>NO_NOTES</answer>
</example>
""".strip()


async def get_request_notes(ctx: RunContext[MainRunContext], user_prompt: str) -> Sequence[str]:
    notes: list[str] = []

    # Strip HTML tags from user prompt to prevent confusing the note-generation model
    user_prompt = html.escape(user_prompt.strip())
    date_and_cwd_note = await process_text(ctx.deps, _get_instruction_contextual_info(ctx), user_prompt)
    if (note := date_and_cwd_note.strip()) != "NO_NOTES":
        notes.append(note)

    return notes
