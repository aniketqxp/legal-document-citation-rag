"""PDF parsing service — font-size heuristic implementation.

Architecture: Strategy Pattern
──────────────────────────────
This module defines the ``DocumentIngestorProtocol`` interface and ships one
concrete implementation: ``PdfPlumberHeuristicIngestor`` (the MVP engine).

To swap in a better engine (e.g., a RagFlow deep-layout parser) in a future
phase, simply create a new class that satisfies ``DocumentIngestorProtocol``
and update the ``get_ingestor()`` factory at the bottom. Nothing else in the
application needs to change.

Heuristic algorithm
───────────────────
1. Open the PDF with pdfplumber and collect all character objects, each of
   which carries font-size metadata alongside its text glyph.
2. Compute the **modal body font size** by sampling characters across the
   first 10 pages (fast, and body font is always established early).
3. Scan every page line-by-line (grouped by ``top`` coordinate):
     • If a line's maximum character font-size ≥ body_size × HEADING_SCALE_FACTOR
       → treat it as a Section Heading; flush the current block and start a new one.
     • Otherwise → accumulate into the current text block.
4. At every page boundary flush any accumulated lines as a ``ParsedBlock``
   so that ``page_number`` metadata is always accurate (critical for citations).
5. ``section_title`` carries forward across page breaks within the same section.

MVP limitations (accepted trade-offs per PROJECT_CONTEXT.md):
  • Text-extractable PDFs only — scanned/image PDFs produce empty blocks.
    The pipeline fails gracefully and marks the document ``failed``.
  • Tables are captured as consecutive text lines, not structured HTML.
    This is acceptable: Gemini 1.5 Pro can reason over tabular text given
    enough surrounding context.
  • Multi-column layouts may produce interleaved text. Rare in standard
    legal contracts; deferred to the deep-parser engine upgrade.
"""

from __future__ import annotations

import io
import logging
import re
import statistics
from dataclasses import dataclass
from typing import Protocol

import pdfplumber

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# A line is classified as a heading if its largest character font-size is
# at least this factor above the document's modal body font size.
HEADING_SCALE_FACTOR: float = 1.2

# Prevent stray large punctuation (e.g., decorative bullets) from being
# misidentified as headings.
MIN_HEADING_LENGTH: int = 3

# Headings are rarely longer than this; caps the ALL-CAPS and numbered
# heuristics to avoid accidentally tagging a full paragraph.
MAX_HEADING_LENGTH: int = 120

# Characters within this many points of each other vertically are grouped
# into the same visual line during char→line reconstruction.
LINE_GROUP_TOLERANCE_PT: float = 2.0

# Pages sampled for body-font-size detection (first N pages).
FONT_SAMPLE_PAGES: int = 10

# Warn when a page yields fewer than this many characters (likely scanned).
SPARSE_PAGE_CHAR_THRESHOLD: int = 50

# Font-name substrings that indicate bold weight (case-insensitive).
# pdfplumber exposes the raw PostScript/OpenType font name (e.g., "TimesNewRomanPS-BoldMT").
BOLD_FONT_MARKERS: tuple[str, ...] = ("bold", "-bd", "heavy", "-hv", "black")

# Regex patterns for legal section numbering schemes that appear at body-text
# size but are unambiguously section headers.
_LEGAL_HEADING_RE: list[re.Pattern[str]] = [
    # "ARTICLE I", "ARTICLE IV — DEFINITIONS", "SECTION 2"
    re.compile(r"^\s*(?:ARTICLE|SECTION|EXHIBIT|SCHEDULE|ANNEX)\s+[\dIVXLCDMivxlcdm]+", re.IGNORECASE),
    # "1.  Definitions", "2. Term and Termination"
    re.compile(r"^\s*\d+\.\s{1,4}[A-Z]"),
    # "1.1 License Grant", "2.3.1 Sub-clause"
    re.compile(r"^\s*\d+(?:\.\d+)+\s+[A-Z]"),
]


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ParsedBlock:
    """A contiguous passage of text beneath a single section heading.

    Attributes:
        page_number:    1-indexed PDF page on which this block *starts*.
                        This is stored verbatim in ``DocumentChunk.page_number``
                        and surfaces as a clickable citation in the frontend.
        section_title:  The nearest Section Heading that precedes this block,
                        or ``None`` if the block appears before the first heading
                        (e.g., document preamble / recitals header).
        text:           Raw extracted text — may span multiple paragraphs but is
                        guaranteed to come from a single section and a single page.
    """
    page_number: int
    section_title: str | None
    text: str


# ── Strategy Interface ────────────────────────────────────────────────────────

class DocumentIngestorProtocol(Protocol):
    """Interface contract for PDF-to-structured-text conversion engines.

    All implementations MUST honour:
      • ``page_number`` is 1-indexed.
      • Returned blocks contain non-empty ``text`` (empty blocks are filtered).
      • ``section_title`` is ``None`` only if no heading precedes the block.

    Concrete implementations shipped:
      • ``PdfPlumberHeuristicIngestor`` — Phase 3 MVP (this module).

    Planned future implementations:
      • ``DeepRagFlowIngestor``  — uses computer vision + layout models for
        table reconstruction and multi-column handling (post-MVP).
    """

    def parse(self, pdf_bytes: bytes) -> list[ParsedBlock]:
        """Parse raw PDF bytes into a sequence of structured text blocks."""
        ...


# ── Concrete Implementation ───────────────────────────────────────────────────

class PdfPlumberHeuristicIngestor:
    """Font-size heuristic PDF parser (MVP engine).

    Robust for single-column, selectable-text legal contracts, which covers
    the vast majority of documents lawyers upload.
    """

    def parse(self, pdf_bytes: bytes) -> list[ParsedBlock]:
        """Parse ``pdf_bytes`` into a list of ``ParsedBlock`` objects.

        Args:
            pdf_bytes: Raw bytes of the PDF file as downloaded from MinIO.

        Returns:
            A non-empty list of ``ParsedBlock`` objects, ordered by page number.

        Raises:
            Exception: Re-raises any unhandled pdfplumber error after logging,
                       so the Celery task can classify it as a pipeline failure.
        """
        blocks: list[ParsedBlock] = []
        last_page_num: int = 0

        try:
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                # ── Pass 1: determine the modal body font size ──────────────
                body_font_size = self._compute_body_font_size(pdf)
                heading_threshold = body_font_size * HEADING_SCALE_FACTOR

                logger.debug(
                    "Body font: %.1f pt  |  Heading threshold: %.1f pt",
                    body_font_size,
                    heading_threshold,
                )

                # ── Pass 2: page-by-page block extraction ───────────────────
                # State carried across pages:
                current_section: str | None = None   # most recent heading seen
                current_lines: list[str] = []         # accumulated body lines
                current_page: int = 1                 # page where accumulation started

                for page_num, page in enumerate(pdf.pages, start=1):
                    last_page_num = page_num
                    chars = page.chars

                    if not chars:
                        raw_text = page.extract_text() or ""
                        if len(raw_text) < SPARSE_PAGE_CHAR_THRESHOLD:
                            logger.warning(
                                "Page %d: no character metadata — "
                                "may be scanned/image-only.",
                                page_num,
                            )
                        continue

                    lines = self._chars_to_lines(chars)

                    for line_chars in lines:
                        line_text = "".join(c["text"] for c in line_chars).strip()
                        if not line_text:
                            continue

                        max_font = max(
                            (c.get("size") or 0.0) for c in line_chars
                        )
                        is_heading = (
                            # Original: font is measurably larger than body text
                            (max_font >= heading_threshold and len(line_text) >= MIN_HEADING_LENGTH)
                            # New: all-caps or numbered legal section pattern
                            or self._is_legal_heading_by_pattern(line_text)
                            # New: entire line rendered in a bold typeface
                            or (self._is_bold_line(line_chars) and len(line_text) <= MAX_HEADING_LENGTH)
                        )

                        if is_heading:
                            # Flush accumulated body text as a block BEFORE
                            # starting the new section.
                            if current_lines:
                                text = "\n".join(current_lines).strip()
                                if text:
                                    blocks.append(ParsedBlock(
                                        page_number=current_page,
                                        section_title=current_section,
                                        text=text,
                                    ))
                                current_lines = []

                            # Advance section state.
                            current_section = line_text
                            current_page = page_num

                        else:
                            # Body text — record the page where this block starts.
                            if not current_lines:
                                current_page = page_num
                            current_lines.append(line_text)

                    # ── Page boundary flush ─────────────────────────────────
                    # Flush at every page end so page_number metadata is precise.
                    # section_title intentionally carries forward across page breaks.
                    if current_lines:
                        text = "\n".join(current_lines).strip()
                        if text:
                            blocks.append(ParsedBlock(
                                page_number=page_num,
                                section_title=current_section,
                                text=text,
                            ))
                        current_lines = []

        except Exception:
            logger.exception("PdfPlumberHeuristicIngestor.parse() raised an exception")
            raise

        logger.info(
            "Parsed %d blocks across %d pages",
            len(blocks),
            last_page_num,
        )
        return blocks

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_bold_line(line_chars: list[dict]) -> bool:
        """Return True if every non-whitespace character in the line is bold.

        Checks the PostScript font name for known bold-weight markers.
        A line of all-bold body-size text is almost always a section header
        in standard legal contract typesetting.
        """
        content_chars = [c for c in line_chars if (c.get("text") or "").strip()]
        if not content_chars:
            return False
        return all(
            any(marker in (c.get("fontname") or "").lower() for marker in BOLD_FONT_MARKERS)
            for c in content_chars
        )

    @staticmethod
    def _is_legal_heading_by_pattern(line_text: str) -> bool:
        """Return True if the line matches a structural legal heading pattern.

        Catches the two most common cases that font-size detection misses:
          1. ALL-CAPS lines (e.g. "DEFINITIONS", "REPRESENTATIONS AND WARRANTIES")
          2. Numbered section prefixes (e.g. "1. Term", "2.3 Payment Obligations",
             "ARTICLE IV — Indemnification")
        """
        stripped = line_text.strip()
        if not (MIN_HEADING_LENGTH <= len(stripped) <= MAX_HEADING_LENGTH):
            return False
        # ALL-CAPS (no lowercase letters present)
        if stripped.isupper():
            return True
        # Structural numbering or keyword prefix
        return any(pattern.match(stripped) for pattern in _LEGAL_HEADING_RE)

    def _compute_body_font_size(self, pdf: "pdfplumber.PDF") -> float:
        """Return the modal font size across the first ``FONT_SAMPLE_PAGES`` pages.

        Sampling strategy:
          • Characters smaller than 5 pt are ignored (footnote superscripts, etc.)
          • Sizes are rounded to 1 decimal place to collapse near-identical sizes
            (e.g., 11.9 and 12.0 map to the same bucket).
          • Falls back to 12.0 pt if no characters are found (e.g., OCR image PDF).
        """
        sizes: list[float] = []
        for page in pdf.pages[:FONT_SAMPLE_PAGES]:
            for char in page.chars:
                size = char.get("size")
                if size and size > 5.0:
                    sizes.append(round(float(size), 1))

        if not sizes:
            logger.warning(
                "No character font data found in first %d pages — "
                "defaulting body font size to 12.0 pt",
                FONT_SAMPLE_PAGES,
            )
            return 12.0

        try:
            return statistics.mode(sizes)
        except statistics.StatisticsError:
            # Multiple equally-common sizes → fall back to median.
            return float(statistics.median(sizes))

    def _chars_to_lines(self, chars: list[dict]) -> list[list[dict]]:
        """Group pdfplumber character dicts into visual lines.

        Groups characters whose ``top`` coordinate is within
        ``LINE_GROUP_TOLERANCE_PT`` of each other into the same line.
        Characters within a line are sorted left-to-right by ``x0``.

        This is preferred over pdfplumber's built-in word/line extraction
        because it preserves per-character font-size data, which is required
        for heading detection.

        Args:
            chars: List of pdfplumber character dicts (from ``page.chars``).

        Returns:
            A list of character-groups, each group representing one visual line,
            sorted top-to-bottom then left-to-right.
        """
        if not chars:
            return []

        # Sort globally: top first, then x0 for left-to-right reading order.
        sorted_chars = sorted(
            chars,
            key=lambda c: (round(c.get("top", 0.0)), c.get("x0", 0.0)),
        )

        lines: list[list[dict]] = []
        current_line: list[dict] = [sorted_chars[0]]
        current_top = round(float(sorted_chars[0].get("top", 0.0)))

        for char in sorted_chars[1:]:
            char_top = round(float(char.get("top", 0.0)))
            if abs(char_top - current_top) <= LINE_GROUP_TOLERANCE_PT:
                current_line.append(char)
            else:
                lines.append(current_line)
                current_line = [char]
                current_top = char_top

        if current_line:
            lines.append(current_line)

        return lines


# ── Factory — single dependency-injection point ───────────────────────────────

def get_ingestor() -> DocumentIngestorProtocol:
    """Return the active document ingestor implementation.

    This is the **only** place that needs to change when upgrading the
    parsing engine. Swap the return value; everything else is untouched.

    Current engine: ``PdfPlumberHeuristicIngestor`` (Phase 3 MVP).
    Future engine:  ``DeepRagFlowIngestor`` (post-MVP, high-fidelity).
    """
    return PdfPlumberHeuristicIngestor()
