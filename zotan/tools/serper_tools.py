"""Serper Search Tool - Provides web search capability for the agent."""

from datetime import datetime
from typing import Any

import aiohttp
from pydantic_ai import RunContext

from ..types_ import MainRunContext, ToolExecutionError


def get_current_date(
    _ctx: RunContext[MainRunContext],
) -> str:
    """Get the current system date and time.

    Returns:
        Current datetime in human-readable format
    """
    return datetime.now().strftime("%Y, %B %d")


async def google_search(
    ctx: RunContext[MainRunContext],
    query: str,
    num_results: int = 10,
    page: int = 1,
    language: str = "en",
    country: str = "us",
) -> str:
    """Search the web using Google search engine.

    Returns title, snippet, and link for each result.

    Do NOT rely on the cut-off date in the training data, which is usually several months before the current date.
    Instead, always use the `get_current_date` tool to determine the current date.

    For detailed research, comparison tasks, or finding specific information, you MUST use the 'page' parameter (e.g., page=2, page=3) to retrieve additional pages.
    NEVER assume the first page of results contain all relevant information.

    NEVER rely solely on Google search snippets for accurate or detailed information.
    The search snippet is often misleading for comparison tasks.
    After identifying relevant results, ALWAYS use `fetch_http` to download the full content of the webpage.

    Args:
        query: The search query string. Be specific and use quotes for exact phrases.
        num_results: Number of results to return per page (default: 10, max: 10).
        page: Page number for pagination. Use higher page numbers to get more results.
        language: Language code for results (e.g., 'en', 'zh-cn').
        country: Country code for results (e.g., 'us', 'cn').

    Returns:
        Search results with title, URL, and snippet for each result.
    """
    api_key = ctx.deps.config.serper_api_key
    assert api_key is not None

    url = "https://google.serper.dev/search"
    payload = {
        "q": query,
        "num": max(1, min(num_results, 10)),
        "page": max(1, page),
        "hl": language,
        "gl": country,
    }
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as response:
                if response.status == 200:
                    data = await response.json()
                    return _format_results(data)
                elif response.status == 401:
                    raise ToolExecutionError("Invalid API key. Please check your Serper API key.")
                elif response.status == 402:
                    raise ToolExecutionError("Insufficient credits. Please check your Serper account balance.")
                elif response.status == 429:
                    raise ToolExecutionError("Rate limit exceeded. Please try again later.")
                else:
                    text = await response.text()
                    raise ToolExecutionError(f"Search failed with status {response.status}: {text}")
    except aiohttp.ClientError as e:
        raise ToolExecutionError(f"{e}")


def _format_results(data: dict[str, Any]) -> str:
    """Format search results into a readable string."""
    formatted = ""

    # Add knowledge graph if available
    if knowledge := data.get("knowledgeGraph"):
        kg_title = knowledge.get("title", "No title")
        kg_desc = knowledge.get("description", "")
        kg_attributes = knowledge.get("attributes", {})
        if kg_title or kg_desc or kg_attributes:
            formatted += f"Knowledge Graph: {kg_title}"
            if kg_desc:
                formatted += f"\n   {kg_desc}"
            if kg_attributes:
                formatted += f"\n   Attributes:"
                for key, value in kg_attributes.items():
                    formatted += f"\n     - {key}: {value}"

    for i, result in enumerate(data.get("organic", []), 1):
        title = result.get("title", "No title")
        link = result.get("link", "No link")
        snippet = result.get("snippet", "No snippet")

        formatted += f"\n\n{i}. {title}"
        formatted += f"\n   URL: {link}"
        formatted += f"\n   {snippet}"

        # Add attributes if available
        if attributes := result.get("attributes"):
            formatted += f"\n   Attributes:"
            for key, value in attributes.items():
                formatted += f"\n     - {key}: {value}"

        # Add sitelinks if available
        if sitelinks := result.get("sitelinks"):
            formatted += f"\n   Sitelinks:"
            for sitelink in sitelinks:
                sl_title = sitelink.get("title", "No title")
                sl_link = sitelink.get("link", "No link")
                sl_snippet = sitelink.get("snippet", "No snippet")
                if sl_title and sl_link:
                    formatted += f"\n     - {sl_title}"
                    formatted += f"\n       URL: {sl_link}"
                    if sl_snippet:
                        formatted += f"\n       {sl_snippet}"

    # Add pagination info if available
    if search_info := data.get("searchInformation"):
        total_results = search_info.get("totalResults", "")
        if total_results:
            formatted += f"\n\nTotal results: {total_results}"

    return formatted.strip()
