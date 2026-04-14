"""Document/Image parsing tools for Zotan agent.

Supports parsing various document or image formats (PDF, DOCX, XLSX, PPTX, PNG, JPEG,
etc.) into Markdown using the LlamaCloud API.
"""

from pathlib import Path
from typing import Literal

from llama_cloud import AsyncLlamaCloud
from magika import ContentTypeLabel
from pydantic_ai import RunContext

from ..text import guess_file_type
from ..types_ import MainRunContext, ToolExecutionError
from .file_tools import authenticate_and_map_path

SUPPORTED_DOCUMENT_TYPES: set[ContentTypeLabel] = {
    ContentTypeLabel.PDF,
    ContentTypeLabel.DOC,
    ContentTypeLabel.DOCX,
    ContentTypeLabel.XLS,
    ContentTypeLabel.XLSX,
    ContentTypeLabel.PPT,
    ContentTypeLabel.PPTX,
    ContentTypeLabel.ODT,
    ContentTypeLabel.ODS,
    ContentTypeLabel.ODP,
    ContentTypeLabel.RTF,
    ContentTypeLabel.EPUB,
    ContentTypeLabel.HTML,
}

SUPPORTED_IMAGE_TYPES: set[ContentTypeLabel] = {
    ContentTypeLabel.PNG,
    ContentTypeLabel.JPEG,
    ContentTypeLabel.GIF,
    ContentTypeLabel.BMP,
    ContentTypeLabel.TIFF,
    ContentTypeLabel.WEBP,
}


async def parse_rich_file(
    ctx: RunContext[MainRunContext],
    path: Path,
    intent: str,
) -> str:
    """Parse a document or image file into Markdown format using LlamaCloud API.

    **Supported formats:**
    - Documents: PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, ODT, ODS, ODP, RTF, EPUB, HTML
    - Images: PNG, JPG, JPEG, GIF, BMP, TIFF, WEBP (processed with OCR)

    Args:
        path: Path to the document or image file. Can be absolute or relative to workspace.
        intent: A description of what information you're looking for from the file.

    Returns:
        Path to Markdown file parsed from the document or image, or an error message if parsing fails.
    """
    if isinstance(real_path := authenticate_and_map_path(ctx, path), str):
        raise ToolExecutionError(f"{real_path}")

    if not real_path.is_file():
        raise ToolExecutionError(f"File not found: {path}")

    type_info = guess_file_type(real_path)
    if type_info is None:
        raise ToolExecutionError(f"{path} has unrecognized file type")

    file_type: Literal["document", "image"]
    if type_info.label in SUPPORTED_DOCUMENT_TYPES:
        file_type = "document"
    elif type_info.label in SUPPORTED_IMAGE_TYPES:
        file_type = "image"
    else:
        raise ToolExecutionError(f"{path} has unsupported file type {type_info.description}")

    # Check for cached Markdown file (same name, .md extension)
    if not (cached_file := real_path.with_suffix(".md")).exists():
        client = AsyncLlamaCloud(api_key=ctx.deps.config.llamacloud_api_key)

        # Upload the document/image
        file = await client.files.create(
            file=real_path,
            purpose="parse",
        )

        # Parse the document/image
        result = await client.parsing.parse(
            tier="agentic_plus" if file_type == "document" else "agentic",  # Use agentic tier for better OCR results
            version="latest",
            file_id=file.id,
            expand=["markdown_full"],  # All pages in a single Markdown file
        )

        if not result.markdown_full:
            raise ToolExecutionError(f"No content extracted from {path} with type {type_info.description}")

        # Save to local cache file
        cached_file.write_text(result.markdown_full, encoding="utf-8")

    return f"{path} with type {type_info.description} was parsed successfully with content written to {path.with_name(cached_file.name)}"
