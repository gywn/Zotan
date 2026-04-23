"""Session management module for Zotan."""

from __future__ import annotations

import dataclasses
from dataclasses import KW_ONLY
from pathlib import Path
from typing import Awaitable, Callable, Literal, Sequence, TypeAlias, TypedDict, TypeVar, Union

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ModelProfile, ModelRequest, ModelResponse, ModelSettings, PartDeltaEvent, PartEndEvent, PartStartEvent, RunContext, ToolReturnPart
from pydantic_ai._run_context import AgentDepsT
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .config import Config, LLMConfig
from .toml import deep_merge_dict


@dataclasses.dataclass
class ToolExecutionPart:
    tool_call_id: str

    _: KW_ONLY

    # -N: pending for user approval with execution order N
    # 0: unresumable
    # N: resumable with execution order N
    exec_order: int = 0
    part_kind: Literal["tool-execution"] = "tool-execution"  # Used as discriminator


class ToolExecutionError(RuntimeError):
    def __init__(self, message: str) -> None:
        self.message = message


@dataclasses.dataclass
class ToolExecution:
    parts: Sequence[ToolExecutionPart | ToolReturnPart]

    _: KW_ONLY

    kind: Literal["tool-execution"] = "tool-execution"
    is_pending: bool = False  # Approval required


StackFrame: TypeAlias = Union[
    ToolExecution,
    ModelRequest,
    PartStartEvent,
    PartDeltaEvent,
    PartEndEvent,
    ModelResponse,
]


Stack: TypeAlias = Sequence[StackFrame]


def get_pending(frame: ToolExecution | ModelRequest) -> bool:
    if isinstance(frame, ToolExecution):
        return frame.is_pending
    else:
        try:
            metadata = TypeAdapter(TypedDict("", {"is_pending": bool})).validate_python(frame.metadata)
        except ValidationError:
            return False
        return metadata["is_pending"]


FrameT = TypeVar("FrameT", ToolExecution, ModelRequest)


def set_pending(frame: FrameT, is_pending: bool) -> FrameT:
    if isinstance(frame, ToolExecution):
        return dataclasses.replace(
            frame,
            is_pending=is_pending,
        )
    else:
        return dataclasses.replace(
            frame,
            metadata=deep_merge_dict(frame.metadata, {"is_pending": is_pending}),
        )


def get_instructive(frame: ModelRequest) -> bool:
    try:
        metadata = TypeAdapter(TypedDict("", {"is_instruction": bool})).validate_python(frame.metadata)
    except ValidationError:
        return False
    return metadata["is_instruction"]


def get_common_prefix_length(left: Stack, right: Stack) -> int:
    for i, (left_frame, right_frame) in enumerate(zip(left, right)):
        if left_frame != right_frame:
            return i
    return min(len(left), len(right))


SpinFunc: TypeAlias = Callable[[RunContext[AgentDepsT], Stack], Awaitable[Stack]] | Callable[[RunContext[AgentDepsT], Stack], Stack]


@dataclasses.dataclass(frozen=True)
class MainRunContext:
    config: Config

    workspace_dir: Path | None = None

    # Sub-agent related
    agent_kind: str = "supervisor"
    parent: RunContext[MainRunContext] | None = None
    spin_ui: SpinFunc[MainRunContext] | None = None

    def __post_init__(self) -> None:
        if self.workspace_dir is not None and not self.workspace_dir.is_dir():
            raise ValueError(f"{self.workspace_dir} is not a directory")


def get_llm_model(llm_config: LLMConfig, thinking: bool = True) -> Model:
    return OpenAIChatModel(
        model_name=llm_config.model_name,
        provider=OpenAIProvider(
            api_key=llm_config.api_key,
            base_url=llm_config.base_url,
        ),
        profile=ModelProfile(
            supports_thinking=True,
        ),
        settings=ModelSettings(
            thinking=thinking,
            extra_body={
                # Only applicable to QWen3.6-plus
                # Whether to append the reasoning content of assistant messages in the dialogue history to the model input. Default: false
                # Must be enabled, otherwise the same things will be constantly rethought
                "preserve_thinking": True,
            },
        ),
    )


@dataclasses.dataclass
class AgentSession:
    stack: Stack
