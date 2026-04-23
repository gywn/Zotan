import re
from pathlib import Path
from typing import Literal

import tiktoken
from magika import ContentTypeInfo, Magika
from rich.console import Console
from rich.markdown import Markdown


def guess_line_ending(text: str) -> Literal["\n", "\r\n", "\r"]:
    counts: dict[Literal["\n", "\r\n", "\r"], int] = {"\n": 0, "\r\n": 0, "\r": 0}
    for m in re.finditer("\r?\n|\r", text):
        counts[m.group(0)] += 1  # type: ignore[reportArgumentType]
    return max(counts.items(), key=lambda p: p[1])[0]


def _fix_rich_background_colors() -> None:
    """
    HACK: ANSI standard only defines foreground colors (0-15), not for backgrounds.
    When Rich's DEFAULT_STYLES includes background color values, they cause poor contrast.
    """
    from rich.default_styles import DEFAULT_STYLES

    for style in DEFAULT_STYLES.values():
        if style.bgcolor not in (None, "default"):
            style._bgcolor = None  # type: ignore[reportPrivateUsage]


_fix_rich_background_colors()


def print_markdown(content: str) -> None:
    Console(color_system="truecolor").print(Markdown(content))


MIN_TOKENS_PER_LINE = 50


def truncate_text_by_tokens(
    text: str,
    max_tokens: int,
    offset: int | None = 1,
    truncation_indicator: str = "[…]",
) -> str:
    # Initialize tokenizer (cl100k_base is efficient and commonly used)
    encoding = tiktoken.get_encoding("cl100k_base")

    # Pre-calculate token overhead for line numbers and truncation indicator
    # This accounts for the fixed-width formatting that will be added to each line
    # When offset is None, line numbers are omitted entirely
    tokens_per_line_number = 0 if offset is None else len(encoding.encode(f"{128:6d}│"))
    tokens_per_indicator = len(encoding.encode(truncation_indicator))

    # Token cache to avoid re-encoding the same lines during binary search
    # Each entry is the tokenized form of a line from the input text
    token_cache: list[list[int]] = []

    def _format_with_limit(max_tokens_per_line: int, head_tail_trimming: int = 0) -> tuple[int, str]:
        """Format text content with token limit and optional head-tail trimming.

        Args:
            max_tokens_per_line: Maximum tokens allowed per line in the output.
            head_tail_trimming: Number of lines to keep from the beginning and end
                when the text is too long. When > 0 and the text has more than
                2*head_tail_trimming lines, only the first and last head_tail_trimming
                lines are shown with a truncation indicator in between.
        """
        content = ""
        lines = text.splitlines()
        line_indices = list(range(len(lines)))
        if head_tail_trimming > 0 and len(lines) > 2 * head_tail_trimming:
            line_indices = line_indices[:head_tail_trimming] + line_indices[-head_tail_trimming:]
        for line_idx in line_indices:
            line = lines[line_idx]
            # Use cached tokenized line if available, otherwise encode and cache
            if line_idx < len(token_cache):
                line_tokens = token_cache[line_idx]
            else:
                line_tokens = encoding.encode(line, disallowed_special=())
                token_cache.append(line_tokens)

            # Add line number prefix (e.g., "     1│") when offset is provided
            # When offset is None, line numbers are omitted entirely (for bash output)
            if offset is not None:
                line_number = line_idx + offset
                content += f"{line_number:6d}│"

            # Check if line fits within the token limit
            available_tokens = max_tokens_per_line - tokens_per_line_number - 1
            if len(line_tokens) <= available_tokens:
                # Line fits completely - add full content
                content += line
            else:
                # Line needs truncation - keep what fits and add indicator
                truncated_tokens = line_tokens[: max(0, available_tokens - tokens_per_indicator - 1)]
                content += encoding.decode(truncated_tokens) + truncation_indicator

            content += "\n"

            if (
                head_tail_trimming > 0
                and len(lines) > 2 * head_tail_trimming
                # Between the head part and tail part
                and line_idx == head_tail_trimming - 1
            ):
                content += f"{truncation_indicator}\n"

        # Return total token count and the formatted content
        return len(encoding.encode(content, disallowed_special=())), content

    # Phase 1: Search on max_tokens_per_line using iterative reduction
    # Start with upper_n = max_tokens, iterate: new_upper_n = (upper_n + MIN_TOKENS_PER_LINE) // 2
    upper_n = max_tokens
    while upper_n > MIN_TOKENS_PER_LINE:
        upper_tokens, upper_content = _format_with_limit(upper_n, 0)
        if upper_tokens <= max_tokens:
            return upper_content
        upper_n = (upper_n + MIN_TOKENS_PER_LINE) // 2

    # Phase 2: Head-tail trimming when upper_n <= MIN_TOKENS_PER_LINE
    # Binary search to find optimal head_tail_trimming
    lower_n = 0
    upper_n = (len(text.splitlines()) + 1) // 2

    # Check edge case: even with 0 lines, do we exceed?
    lower_tokens, lower_content = _format_with_limit(MIN_TOKENS_PER_LINE, 1)
    if lower_tokens > max_tokens:
        # Even minimal output exceeds limit - return the most truncated version
        return lower_content

    # Binary search: narrow the bounds until they're adjacent
    while upper_n > lower_n + 1:
        mid_n = (lower_n + upper_n) // 2
        mid_tokens, mid_content = _format_with_limit(MIN_TOKENS_PER_LINE, mid_n)

        if mid_tokens > max_tokens:
            # Still too many tokens - need to reduce head_tail_trimming
            upper_n, upper_tokens, upper_content = mid_n, mid_tokens, mid_content
        else:
            # Fits within limit - try to increase head_tail_trimming
            lower_n, lower_tokens, lower_content = mid_n, mid_tokens, mid_content

    # Return the best result found
    return lower_content


_magika = Magika()


def guess_file_type(path: Path) -> ContentTypeInfo | None:
    try:
        return _magika.identify_path(path).output  # type: ignore[reportUnknownMemberType]
    except IndexError:
        return None


def is_source_code_file(type_info: ContentTypeInfo) -> bool:
    return type_info.label in {
        label
        for label, info in _magika._cts_infos.items()  # type: ignore[reportPrivateUsage]
        if info.group == "code"
    }  # fmt: skip
