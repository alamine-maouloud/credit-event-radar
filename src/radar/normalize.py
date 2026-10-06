"""Deterministic HTML to text normalisation for SEC filings.

The normaliser is versioned because evidence offsets refer to its output: a change here
invalidates every stored span, so the version string must change with the behaviour.

Rules (version sec-html-1.0):

- bytes are decoded as UTF-8, then as the declared charset, then as cp1252 with replacement;
- ``script``, ``style``, ``head``, ``noscript``, ``ix:header`` and any element whose inline
  style hides it (``display:none``) are dropped, which removes the hidden iXBRL header;
  comments, doctype and XML declarations are dropped too;
- block-level elements start and end a line, table cells are joined with " | ", inline
  elements never break a word;
- text is NFC-normalised, Unicode spaces become plain spaces, zero-width characters are
  removed, runs of spaces collapse, lines are stripped, empty lines are dropped.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Protocol

from bs4 import BeautifulSoup, NavigableString, Tag
from bs4.element import PreformattedString

NORMALIZER_VERSION = "sec-html-1.0"

DROP_TAGS = frozenset({"script", "style", "head", "noscript", "ix:header", "template"})
BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "thead",
        "tbody", "tfoot", "ul", "ol", "blockquote", "section", "article", "header", "footer",
        "pre", "hr", "title", "dl", "dt", "dd", "caption", "address", "main", "nav",
    }
)  # fmt: skip
CELL_TAGS = frozenset({"td", "th"})
CELL_SEPARATOR = " | "

_HIDDEN_RE = re.compile(r"display\s*:\s*none", re.IGNORECASE)
_CHARSET_RE = re.compile(rb"charset=[\"']?([A-Za-z0-9_\-]+)", re.IGNORECASE)
_UNICODE_SPACES = dict.fromkeys(
    map(ord, "               　"),
    " ",
)  # fmt: skip
_ZERO_WIDTH_RE = re.compile("[​‌‍⁠﻿]")
_SPACES_RE = re.compile(r"[ \t\r\f\v]+")


class Normalizer(Protocol):
    version: str

    def normalize(self, raw: bytes) -> str: ...


def decode_bytes(raw: bytes) -> str:
    """Decode HTML bytes deterministically: UTF-8, declared charset, then cp1252."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    match = _CHARSET_RE.search(raw[:4096])
    if match:
        try:
            return raw.decode(match.group(1).decode("ascii"))
        except (UnicodeDecodeError, LookupError):
            pass
    return raw.decode("cp1252", errors="replace")


def _is_hidden(tag: Tag) -> bool:
    style = tag.get("style")
    return bool(style) and bool(_HIDDEN_RE.search(str(style)))


def _sec_dropped(tag: Tag) -> bool:
    return (tag.name or "").lower() in DROP_TAGS or _is_hidden(tag)


def _walk(node: Tag, out: list[str], dropped: Callable[[Tag], bool] = _sec_dropped) -> None:
    for child in node.children:
        if isinstance(child, PreformattedString):
            continue  # comments, doctype, XML declarations, CDATA
        if isinstance(child, NavigableString):
            out.append(str(child))
            continue
        if not isinstance(child, Tag):
            continue
        name = (child.name or "").lower()
        if dropped(child):
            continue
        if name in CELL_TAGS:
            out.append(" ")
            _walk(child, out, dropped)
            out.append(CELL_SEPARATOR)
        elif name in BLOCK_TAGS:
            out.append("\n")
            _walk(child, out, dropped)
            out.append("\n")
        else:
            _walk(child, out, dropped)


def html_to_text(html: str, dropped: Callable[[Tag], bool] = _sec_dropped) -> str:
    """Flatten HTML into line-oriented text, before whitespace normalisation."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    _walk(soup, out, dropped)
    return "".join(out)


# ------------------------------------------------------- issuer web pages --- #

IR_NORMALIZER_VERSION = "ir-html-1.1"
_IR_DROP_TAGS = DROP_TAGS | {"nav", "aside", "footer", "form", "button", "iframe", "svg"}
_IR_CONTENT_TAGS = frozenset({"html", "body", "main", "article"})
_IR_DROP_ATTR_RE = re.compile(
    r"related|teaser|slider|carousel|recommend|cookie|breadcrumb|sidebar|share-bar|social-share|newsletter",
    re.IGNORECASE,
)


def _ir_dropped(tag: Tag) -> bool:
    """Issuer pages: drop navigation, footers, asides, forms and 'related content' blocks
    (teasers of other releases) identified by their id, class or role."""
    name = (tag.name or "").lower()
    if name in _IR_DROP_TAGS or _is_hidden(tag):
        return True
    if name == "header" and tag.find("nav") is not None:
        return True
    if name in _IR_CONTENT_TAGS:
        # Semantic content wrappers are never dropped by their attributes: OMV wraps the whole
        # article in <article class="... has-sidebar ..."> (ir-html-1.1, the 1.0 rule dropped it).
        return False
    attrs = " ".join(
        str(v) if not isinstance(v, list) else " ".join(v)
        for k, v in tag.attrs.items()
        if k in ("id", "class", "role", "data-component")
    )
    if attrs and (_IR_DROP_ATTR_RE.search(attrs) or "navigation" in attrs.lower()):
        return True
    return False


def normalize_ir_html(raw: bytes) -> str:
    return normalize_text(html_to_text(decode_bytes(raw), _ir_dropped))


class IrHtmlNormalizer:
    """Normaliser for issuer investor-relations pages (press releases, ratings pages)."""

    version = IR_NORMALIZER_VERSION

    def normalize(self, raw: bytes) -> str:
        return normalize_ir_html(raw)


def normalize_text(text: str) -> str:
    """Canonical whitespace and Unicode form. Idempotent."""
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_UNICODE_SPACES)
    text = _ZERO_WIDTH_RE.sub("", text)
    lines: list[str] = []
    for line in text.split("\n"):
        line = _SPACES_RE.sub(" ", line).strip()
        if line.endswith(CELL_SEPARATOR.strip()):
            line = line[: -len(CELL_SEPARATOR.strip())].rstrip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalize_sec_html(raw: bytes) -> str:
    return normalize_text(html_to_text(decode_bytes(raw)))


class SecHtmlNormalizer:
    """Normaliser for SEC EDGAR HTML and iXBRL documents."""

    version = NORMALIZER_VERSION

    def normalize(self, raw: bytes) -> str:
        return normalize_sec_html(raw)


PDF_NORMALIZER_VERSION = "pdf-text-1.1"


PDF_PARAGRAPH_MAX_CHARS = 2000
_TERMINAL = (".", "!", "?", ":", ";")


def pdf_to_text(raw: bytes) -> str:
    """One line per paragraph (PyMuPDF text blocks), lines of a block joined with spaces.

    Some PDFs split a sentence across blocks: a block that does not end with terminal
    punctuation is joined with the next one (up to PDF_PARAGRAPH_MAX_CHARS), so the
    sentence splitter sees whole sentences.
    """
    import pymupdf

    paragraphs: list[str] = []
    with pymupdf.open(stream=raw, filetype="pdf") as document:
        for page in document:
            for block in page.get_text("blocks"):
                if block[6] != 0:
                    continue  # image block
                lines = [line.strip() for line in str(block[4]).split("\n") if line.strip()]
                if not lines:
                    continue
                text = " ".join(lines)
                if (
                    paragraphs
                    and not paragraphs[-1].endswith(_TERMINAL)
                    and len(paragraphs[-1]) + len(text) <= PDF_PARAGRAPH_MAX_CHARS
                ):
                    paragraphs[-1] = paragraphs[-1] + " " + text
                else:
                    paragraphs.append(text)
    return "\n".join(paragraphs)


def normalize_pdf(raw: bytes) -> str:
    return normalize_text(pdf_to_text(raw))


class PdfNormalizer:
    """Normaliser for PDF documents. Output depends on the PyMuPDF text extractor, so the
    version string is bumped whenever the library or the rules change."""

    version = PDF_NORMALIZER_VERSION

    def normalize(self, raw: bytes) -> str:
        return normalize_pdf(raw)


def is_pdf(raw: bytes) -> bool:
    return raw[:5] == b"%PDF-"


def normalizer_for(content_type: str | None, raw: bytes, *, kind: str = "edgar") -> Normalizer:
    """Pick the normaliser from the content type (PDF sniffed from the bytes) and the
    source kind: ``edgar`` keeps the SEC rules, ``ir`` drops site chrome and teasers."""
    media = (content_type or "").split(";")[0].strip().lower()
    if media == "application/pdf" or is_pdf(raw):
        return PdfNormalizer()
    if kind == "ir":
        return IrHtmlNormalizer()
    return SecHtmlNormalizer()
