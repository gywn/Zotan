"""HTTP Fetch Tool - Download and extract text from web pages."""

import html
import html.parser
import re
import urllib.parse
from pathlib import Path
from typing import cast

import curl_cffi.requests.exceptions
import tiktoken
from curl_cffi import CurlOpt, Response, Session
from html2text import HTML2Text
from pydantic_ai import RunContext
from w3lib.encoding import html_to_unicode
from w3lib.html import replace_entities

from ..firefox import read_firefox_cookies
from ..text import truncate_text_by_tokens
from ..types_ import MainRunContext, ToolExecutionError


def is_valid_url(url: str) -> bool:
    """Validate URL format."""
    try:
        result = urllib.parse.urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except ValueError:
        return False


def extract_title(html_str: str) -> str:
    """Extract page title from HTML content using html.parser."""

    class TitleExtractor(html.parser.HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.title: str | None = None
            self.in_title = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "title":
                self.in_title = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "title":
                self.in_title = False

        def handle_data(self, data: str) -> None:
            if self.in_title and self.title is None:
                self.title = data

    parser = TitleExtractor()
    parser.feed(html_str)

    if parser.title:
        return html.unescape(parser.title).strip()

    return "No title"


def _get_new_session(firefox_profile: Path | None, doh_url: str | None) -> Session[Response]:
    """Get or create a persistent curl_cffi session with cookies.

    Note: curl_cffi uses BoringSSL with embedded TLS fingerprints to impersonate
    browsers. It does NOT require libnss3 or the curl-impersonate binary.
    The impersonation works by crafting TLS Client Hello messages that exactly
    match Firefox's format (cipher suites, extensions, session IDs, etc.).
    """
    curl_options = {}
    if doh_url is not None:
        curl_options = {CurlOpt.DOH_URL: doh_url}
    session = Session(impersonate="firefox", curl_options=curl_options)
    # Load cookies from Firefox profile if configured
    if firefox_profile and (firefox_profile / "cookies.sqlite").is_file():
        session.cookies = read_firefox_cookies(firefox_profile)
    return session


def _fetch_page(url: str, session: Session[Response], timeout: int = 30) -> tuple[str, str]:
    """Fetch and parse HTML page in blocking fashion."""
    # Only accept HTML content types since we use html2text
    # This prevents servers from returning JSON or other unexpected types
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
    }
    try:
        response = session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
    except curl_cffi.requests.exceptions.RequestException as e:
        return "", str(e)

    if response.status_code == 200:
        # Get Content-Type header for encoding detection
        content_type = response.headers.get("Content-Type")
        # Use w3lib for proper encoding detection (BOM → HTTP header → meta tags → fallback)
        _, html_str = html_to_unicode(content_type, response.content)
        html_str = replace_entities(html_str)
        return html_str, ""
    elif response.status_code == 404:
        return "", f"Page not found (404): {url}"
    elif response.status_code == 403:
        return "", f"Access forbidden (403): {url}. The site may be blocking automated access."
    elif response.status_code == 429:
        return "", f"Rate limited (429): Too many requests. Please try again later."
    else:
        return "", f"HTTP error {response.status_code}: {url}"


def _format_page_content(html_str: str, max_tokens: int) -> tuple[str, bool]:
    """Convert HTML to markdown-formatted text with token-aware processing.

    Args:
        html_str: HTML content as string (already decoded)
        max_tokens: Maximum length of extracted text in tokens.

    Returns:
        Tuple of (converted text, was_truncated flag)
    """
    html2text = HTML2Text()
    html2text.body_width = 0  # No line wrapping
    html2text.ignore_links = False  # Include links in output
    html2text.inline_links = False  # Use reference-style links [text][1] instead of inline [text](url)
    html2text.links_each_paragraph = True  # Put the links after each paragraph instead of at the end.
    html2text.ignore_images = True  # Skip images entirely
    html2text.unicode_snob = True  # Use Unicode characters instead of their ascii pseudo-replacements

    text = html2text.handle(html_str)

    # Check token count and retry without links if exceeding limit
    if len(tiktoken.get_encoding("cl100k_base").encode(text, disallowed_special=())) > max_tokens:
        html2text.ignore_links = True
        text = html2text.handle(html_str)

    text = re.sub(r" +$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # The main truncation to fit the content into the context window
    truncated = truncate_text_by_tokens(text, offset=None, max_tokens=max_tokens)

    return truncated, truncated != text


def fetch_http(
    ctx: RunContext[MainRunContext],
    url: str,
    intent: str,
) -> str:
    """Fetch the HTML from the given URL and extract readable text and links.

    Downloads the HTML from the given URL and extracts readable text and links,
    filtering out HTML tags, scripts, styles, and other non-content elements.
    The output uses reference-style links (e.g., [text][1]) with the full URLs listed at the end of each paragraph.

    Args:
        url: The URL of the web page to fetch. Must be a valid HTTP/HTTPS URL.
        intent: A description of what information you're looking for from the web page.

    Returns:
        Extracted text content from the web page, or an error message.
    """
    if not is_valid_url(url):
        raise ToolExecutionError(f"Invalid URL: {url}. Please provide a valid HTTP/HTTPS URL.")

    # Get or create session with Firefox profile
    if ctx.metadata is None:
        ctx.metadata = dict()
    if isinstance(session_ := ctx.metadata.get("fetch_http"), Session):
        session = cast(Session[Response], session_)
    else:
        session = _get_new_session(
            firefox_profile=ctx.deps.config.firefox_profile,
            doh_url="https://cloudflare-dns.com/dns-query",
        )
        ctx.metadata["fetch_http"] = session

    html_str, error = _fetch_page(url, session, 30)

    if error:
        raise ToolExecutionError(f"{error}")

    content, is_truncated = _format_page_content(html_str, max_tokens=int(20_000))
    content = "\n".join(
        [
            f"Title: {extract_title(html_str)}",
            f"URL: {url}",
            "\nNote: The content is truncated." if is_truncated else "",
            "Content:",
            content,
        ]
    )

    return content
