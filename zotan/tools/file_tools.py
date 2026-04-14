"""File tools for Zotan agent."""

import re
from pathlib import Path

from pydantic_ai import RunContext, Tool

from ..config import WORKING_MODE, WORKSPACE
from ..text import guess_file_type, guess_line_ending, is_source_code_file, truncate_text_by_tokens
from ..types_ import MainRunContext, ToolExecutionError


def authenticate_and_map_path(ctx: RunContext[MainRunContext], path: Path) -> Path | str:
    assert ctx.deps.workspace_dir is not None
    if not path.is_absolute():
        return ctx.deps.workspace_dir / path
    elif WORKING_MODE == "container":
        return path
    try:
        return ctx.deps.workspace_dir / path.relative_to(WORKSPACE)
    except ValueError:
        return f"Do not read or write files outside {WORKSPACE}."


def read_file(
    ctx: RunContext[MainRunContext],
    path: Path,
    intent: str,
    offset: int = 1,
    limit: int | None = None,
) -> str:
    """This tool reads the content of a file.

    Supports reading partial content via `offset` and `limit` parameters.

    Args:
        path: Path to the file to read. Can be absolute or relative to workspace.
        intent: A description of what information you're looking for in the file.
        offset: Line number to start reading from (1-indexed). Default is 1.
        limit: Maximum number of lines to read. Default is None.

    Returns:
        File content as a string, with line numbers prepended for source code files.
        Returns error message if file cannot be read.
    """
    if isinstance(real_path := authenticate_and_map_path(ctx, path), str):
        raise ToolExecutionError(f"{real_path}")

    if not real_path.is_file():
        raise ToolExecutionError(f"File not found: {path}")

    type_info = guess_file_type(real_path)
    _is_source_code_file = type_info is not None and is_source_code_file(type_info)

    try:
        lines = real_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (PermissionError, UnicodeDecodeError) as e:
        raise ToolExecutionError(f"Failed to read file {path}: {e}")

    if offset > len(lines):
        raise ToolExecutionError(f"`start` exceeds number of lines: {len(lines)}")

    # Apply offset and limit
    start = max(0, offset - 1)
    end = min(start + limit, len(lines)) if limit is not None else len(lines)

    # Apply token truncation if needed (adds line numbers internally)
    return truncate_text_by_tokens(
        text="".join(lines[start:end]),
        max_tokens=int(20_000),
        offset=start + 1 if (offset != 1 or limit is not None or _is_source_code_file) else None,
    )


def edit_file(
    ctx: RunContext[MainRunContext],
    path: Path,
    old_content: str,
    new_content: str,
    start_line: int | None = None,
) -> str:
    """This tool edits the content of a file.

    Supports search and replace operations. Can also be used to delete content
    by providing an empty string as new_content.

    Args:
        path: Path to the file to edit. Can be absolute or relative to workspace.
        old_content: The text to search for and replace. Must match exactly.
        new_content: The replacement text. Use empty string to delete content.
        start_line: Optional line number (1-indexed) to start searching from.
            If not specified, searches from the beginning of the file.

    Returns:
        Success message, or error message if operation fails.
    """
    if isinstance(real_path := authenticate_and_map_path(ctx, path), str):
        raise ToolExecutionError(f"{real_path}")

    if not real_path.is_file():
        raise ToolExecutionError(f"File not found: {path}")

    try:
        content = real_path.read_text(encoding="utf-8")
    except (PermissionError, UnicodeDecodeError) as e:
        raise ToolExecutionError(f"Failed to edit file {path}: {e}")

    search_start = 0
    if start_line is not None and start_line > 1:
        lines = content.splitlines(keepends=True)
        if start_line <= len(lines):
            for i in range(start_line - 1):
                search_start += len(lines[i])

    if new_content == "":
        # In case of deletion, the following line break should also be removed
        old_content = re.sub(r"(\r?\n|\r)?$", "\n", old_content)

    # Takes all types of line-endings into account
    old_content_pattern = re.compile(re.sub(r"(\\\r)?\\\n|\\\r", r"(\\r?\\n|\\r)", re.escape(old_content)))
    if (match := old_content_pattern.search(content, pos=search_start)) is None:
        raise ToolExecutionError(f"Content to replace not found in file {path}")

    # Automatically adjust to the line-endings in the file
    new_content = re.sub(r"\r?\n|\r", guess_line_ending(content), new_content)
    content = content[: match.start()] + new_content + content[match.end() :]

    real_path.write_text(content, encoding="utf-8")

    return f"Success: {"Delete" if new_content == "" else "Replace"} completed"


def _write_file(
    ctx: RunContext[MainRunContext],
    path: Path,
    content: str,
) -> str:
    """Write content to a file.

    Creates a new file or overwrites an existing file with the given content.

    Args:
        path: Path to the file. Can be absolute or relative to workspace.
        content: The text content to write to the file.

    Returns:
        Success message, or error message if operation fails.
    """
    if isinstance(real_path := authenticate_and_map_path(ctx, path), str):
        raise ToolExecutionError(f"{real_path}")

    # Create parent directories
    try:
        real_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise ToolExecutionError(f"Failed to create directory: {e}")

    real_path.write_text(content, encoding="utf-8")

    return f"Success: Written to file {path}"


write_file = Tool(
    _write_file,
    name="write_file",
    max_retries=0,
)
