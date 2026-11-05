"""Dependency-free Markdown -> terminal renderer.

The interactive ``chat`` command previously streamed the model's raw Markdown
straight to stdout, forcing the user to mentally parse ``#``, ``**`` and fenced
code blocks.  This module turns that Markdown into readable terminal output:

* when the destination supports ANSI colours (a TTY without ``NO_COLOR``), it
  emits styled text (headings, bold/italic, inline code, code blocks, lists,
  quotes, tables, links, rules);
* otherwise it degrades to clean plain text with the Markdown syntax stripped,
  so redirected output and the offline test-suite stay readable.

It intentionally has no third-party dependencies: the repository forbids
installing packages into Esri's base Python environment.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import unicodedata
from typing import List, Tuple

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
UNDERLINE = "\033[4m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"
CODE_BG = "\033[48;5;236m"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*$")
_BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_NUMBER_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
_RULE_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([A-Za-z0-9_+#.-]*)\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_INLINE_RE = re.compile(
    r"(\*\*.+?\*\*|__.+?__|\*[^*]+?\*|_[^_]+?_|`[^`]+`|\[[^\]]+\]\([^)]+\))"
)


def supports_color(stream=None) -> bool:
    """Return True when ANSI styling should be emitted for ``stream``."""
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    if os.environ.get("ARCGIS_AGENT_NO_COLOR"):
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def _enable_windows_vt() -> None:
    """Best-effort enable of ANSI escape processing on legacy Windows consoles."""
    if os.name != "nt":
        return
    try:
        os.system("")  # noqa: the empty command flips the console into VT mode
    except Exception:
        pass


def terminal_width(default: int = 88, maximum: int = 110) -> int:
    width = shutil.get_terminal_size((default, 20)).columns
    return max(40, min(width - 2, maximum))


def _display_width(text: str) -> int:
    """Terminal column width, counting East-Asian wide/fullwidth glyphs as 2."""
    width = 0
    for ch in text:
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def _clip_display(text: str, max_width: int) -> str:
    """Clip ``text`` to ``max_width`` terminal columns, appending an ellipsis."""
    if _display_width(text) <= max_width:
        return text
    if max_width <= 1:
        return "…"[:max_width]
    out = ""
    used = 0
    for ch in text:
        cw = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + cw > max_width - 1:
            break
        out += ch
        used += cw
    return out + "…"


def _inline_plain(text: str) -> str:
    """Strip inline Markdown syntax, keeping readable text (plain-text mode)."""
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"\*([^*]+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    return text


def _inline_ansi(text: str) -> str:
    """Convert inline Markdown within a single line to ANSI-styled text."""
    out: List[str] = []
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            out.append(text[pos:match.start()])
        token = match.group(0)
        out.append(_style_token(token))
        pos = match.end()
    if pos < len(text):
        out.append(text[pos:])
    return "".join(out)


def _style_token(token: str) -> str:
    if token.startswith("`") and token.endswith("`"):
        return f"{CODE_BG}{YELLOW}{token[1:-1]}{RESET}"
    if token.startswith("**") and token.endswith("**"):
        return f"{BOLD}{_inline_ansi(token[2:-2])}{RESET}"
    if token.startswith("__") and token.endswith("__"):
        return f"{BOLD}{_inline_ansi(token[2:-2])}{RESET}"
    if token.startswith("*") and token.endswith("*") and len(token) > 2:
        return f"{ITALIC}{_inline_ansi(token[1:-1])}{RESET}"
    if token.startswith("_") and token.endswith("_") and len(token) > 2:
        return f"{ITALIC}{_inline_ansi(token[1:-1])}{RESET}"
    link = re.match(r"\[([^\]]+)\]\(([^)]+)\)", token)
    if link:
        return f"{UNDERLINE}{CYAN}{link.group(1)}{RESET}{DIM} ({link.group(2)}){RESET}"
    return token


def _render_inline(text: str, color: bool) -> str:
    return _inline_ansi(text) if color else _inline_plain(text)


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def render_markdown(markdown: str, *, color: bool | None = None, width: int | None = None) -> str:
    """Render a Markdown string for terminal display."""
    if not isinstance(markdown, str):
        markdown = str(markdown)
    if color is None:
        color = supports_color()
    if color:
        _enable_windows_vt()
    if width is None:
        width = terminal_width()

    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: List[str] = []
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]

        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            lang = fence.group(2)
            index += 1
            code_lines: List[str] = []
            while index < total and not lines[index].strip().startswith(marker):
                code_lines.append(lines[index])
                index += 1
            index += 1  # skip closing fence (or run past end)
            out.append(_render_code(code_lines, lang, color, width))
            continue

        if _RULE_RE.match(line) and line.strip():
            out.append((f"{DIM}" + "─" * width + RESET) if color else "-" * width)
            index += 1
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip()
            out.append(_render_heading(text, level, color, width))
            index += 1
            continue

        # Table: a row containing '|' followed by a separator row.
        if "|" in line and index + 1 < total and _TABLE_SEP_RE.match(lines[index + 1]) and "-" in lines[index + 1]:
            table_lines = [line]
            index += 2  # skip header + separator
            while index < total and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            out.append(_render_table(table_lines, color, width))
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            block = [quote.group(1)]
            index += 1
            while index < total:
                nxt = _QUOTE_RE.match(lines[index])
                if not nxt:
                    break
                block.append(nxt.group(1))
                index += 1
            out.append(_render_quote(block, color, width))
            continue

        bullet = _BULLET_RE.match(line)
        number = _NUMBER_RE.match(line)
        if bullet or number:
            text, index = _render_list(lines, index, color, width)
            out.append(text)
            continue

        if not line.strip():
            index += 1
            continue

        # Paragraph: gather consecutive non-special, non-blank lines.
        para = [line]
        index += 1
        while index < total and lines[index].strip() and not _is_block_start(lines[index], index, lines):
            para.append(lines[index])
            index += 1
        out.append(_render_paragraph(para, color, width))

    return "\n".join(block for block in out if block is not None).rstrip("\n")


def _is_block_start(line: str, index: int, lines: List[str]) -> bool:
    if _FENCE_RE.match(line) or _HEADING_RE.match(line) or _RULE_RE.match(line):
        return True
    if _BULLET_RE.match(line) or _NUMBER_RE.match(line) or _QUOTE_RE.match(line):
        return True
    if "|" in line and index + 1 < len(lines) and _TABLE_SEP_RE.match(lines[index + 1]) and "-" in lines[index + 1]:
        return True
    return False


def _render_heading(text: str, level: int, color: bool, width: int) -> str:
    if not color:
        body = _inline_plain(text)
        if level <= 2:
            return body + "\n" + "-" * min(width, max(_display_width(body), 3))
        return body
    if level == 1:
        rule = "━" * min(width, max(_display_width(text), 3))
        return f"{BOLD}{CYAN}{_inline_ansi(text)}{RESET}\n{DIM}{rule}{RESET}"
    if level == 2:
        rule = "─" * min(width, max(_display_width(text), 3))
        return f"{BOLD}{BLUE}{_inline_ansi(text)}{RESET}\n{DIM}{rule}{RESET}"
    palette = {3: GREEN, 4: YELLOW, 5: MAGENTA, 6: GREY}
    col = palette.get(level, GREY)
    return f"{BOLD}{col}{_inline_ansi(text)}{RESET}"


def _render_code(code_lines: List[str], lang: str, color: bool, width: int) -> str:
    body = "\n".join(code_lines).rstrip("\n")
    if not color:
        head = f"[code{':' + lang if lang else ''}]"
        return head + "\n" + body
    label = f" {lang} " if lang else ""
    top = f"{DIM}┌─{label}{'─' * max(0, width - len(label) - 2)}┐{RESET}"
    rendered = [top]
    for cl in code_lines:
        rendered.append(f"{DIM}│{RESET} {cl}")
    rendered.append(f"{DIM}└{'─' * max(0, width - 1)}┘{RESET}")
    return "\n".join(rendered)


def _render_quote(block: List[str], color: bool, width: int) -> str:
    text = " ".join(_inline_plain(b) if not color else b for b in block).strip()
    wrapped = textwrap.wrap(text, width=max(10, width - 4)) or [""]
    if not color:
        return "\n".join("> " + _inline_plain(w) for w in wrapped)
    return "\n".join(f"{DIM}▐{RESET} {ITALIC}{GREY}{_inline_ansi(w)}{RESET}" for w in wrapped)


def _render_list(lines: List[str], start: int, color: bool, width: int) -> Tuple[str, int]:
    rendered: List[str] = []
    index = start
    total = len(lines)
    while index < total:
        line = lines[index]
        if not line.strip():
            # A blank line ends the list unless the next line is also a list item.
            if index + 1 < total and (_BULLET_RE.match(lines[index + 1]) or _NUMBER_RE.match(lines[index + 1])):
                index += 1
                continue
            break
        bullet = _BULLET_RE.match(line)
        number = _NUMBER_RE.match(line)
        if not bullet and not number:
            break
        if bullet:
            indent = len(bullet.group(1))
            marker = "•"
            text = bullet.group(2)
        else:
            indent = len(number.group(1))
            marker = number.group(2) + "."
            text = number.group(3)
        depth = indent // 2
        pad = "  " * depth
        marker_str = f"{CYAN}{marker}{RESET}" if color else marker
        body = _render_inline(text, color)
        hanging = len(pad) + len(marker) + 1
        first = f"{pad}{marker_str} {body}"
        rendered.append(first)
        index += 1
        # Fold lazily-continued lines (indented, not a new item) into this item.
        while index < total and lines[index].strip() and not _is_block_start(lines[index], index, lines) \
                and (lines[index].startswith("  ") or lines[index].startswith("\t")):
            cont = _render_inline(lines[index].strip(), color)
            rendered.append(" " * hanging + cont)
            index += 1
    return "\n".join(rendered), index


def _render_table(table_lines: List[str], color: bool, width: int) -> str:
    rows = [_split_row(line) for line in table_lines]
    if not rows:
        return ""
    cols = max(len(row) for row in rows)
    rows = [row + [""] * (cols - len(row)) for row in rows]
    plain = [[_inline_plain(cell) for cell in row] for row in rows]
    widths = [max(_display_width(plain[r][c]) for r in range(len(plain))) for c in range(cols)]
    # Shrink columns proportionally if the table exceeds the available width.
    budget = width - (3 * cols + 1)
    if budget > 0 and sum(widths) > budget:
        scale = budget / sum(widths)
        widths = [max(3, int(w * scale)) for w in widths]

    def cell_text(c: int, text: str) -> str:
        return _clip_display(text, widths[c])

    def pad(text: str, target: int) -> str:
        return text + " " * max(0, target - _display_width(text))

    def plain_row(cells: List[str]) -> str:
        parts = [" " + pad(cell_text(c, cells[c]), widths[c]) + " " for c in range(cols)]
        return "|" + "|".join(parts) + "|"

    out: List[str] = []
    if color:
        edge = f"{DIM}│{RESET}"
        head = []
        for c in range(cols):
            text = cell_text(c, plain[0][c])
            head.append(f" {BOLD}{text}{RESET} " + " " * max(0, widths[c] - _display_width(text)))
        out.append(edge + edge.join(head) + edge)
        out.append(f"{DIM}├{'┼'.join('─' * (w + 2) for w in widths)}┤{RESET}")
        for row in plain[1:]:
            body = []
            for c in range(cols):
                text = cell_text(c, row[c])
                body.append(" " + pad(text, widths[c]) + " ")
            out.append(edge + edge.join(body) + edge)
    else:
        out.append(plain_row(plain[0]))
        out.append("+" + "+".join("-" * (w + 2) for w in widths) + "+")
        for row in plain[1:]:
            out.append(plain_row(row))
    return "\n".join(out)


def _render_paragraph(para: List[str], color: bool, width: int) -> str:
    joined = " ".join(p.strip() for p in para)
    if not color:
        wrapped = textwrap.wrap(_inline_plain(joined), width=width,
                                break_long_words=False, break_on_hyphens=False)
        return "\n".join(wrapped or [""])
    return "\n".join(_wrap_ansi(joined, width))


def _wrap_ansi(text: str, width: int) -> List[str]:
    """Wrap a line containing inline Markdown on word boundaries, then style it.

    Wrapping measures the visible length (markers excluded) so ``**`` and back
    ticks do not push lines past the terminal width; each wrapped line is styled
    independently, which keeps the common case correct without a full parser.
    """
    words = text.split(" ")
    lines: List[str] = []
    current = ""
    current_len = 0
    for word in words:
        wlen = _display_width(_inline_plain(word))
        if current and current_len + 1 + wlen > width:
            lines.append(_inline_ansi(current))
            current = word
            current_len = wlen
        else:
            if current:
                current += " "
                current_len += 1
            current += word
            current_len += wlen
    if current:
        lines.append(_inline_ansi(current))
    return lines or [""]
