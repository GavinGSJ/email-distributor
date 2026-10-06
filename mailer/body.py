"""Body text <-> HTML helpers and body file loading."""

import re
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from pathlib import Path

from .errors import ConfigError

HTML_SUFFIXES = {".html", ".htm"}


@dataclass(frozen=True)
class Body:
    text: str
    is_html: bool


def load_body(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise ConfigError(f"找不到正文文件: {path}")
    except UnicodeDecodeError:
        raise ConfigError(f"正文文件需要是 UTF-8 编码: {path}")
    return Body(text=text, is_html=path.suffix.lower() in HTML_SUFFIXES)


def text_to_html(text):
    """Blank line -> new paragraph, single newline -> <br>."""
    text = text.replace("\r\n", "\n").strip()
    paragraphs = re.split(r"\n\s*\n", text)
    return "".join(
        '<p style="margin:0 0 1em 0">' + escape(p).replace("\n", "<br>") + "</p>"
        for p in paragraphs
        if p.strip()
    )


class _TextExtractor(HTMLParser):
    _BLOCK = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
    _SKIP = {"style", "script"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self._skip -= 1
        elif tag in self._BLOCK and tag != "br":
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append("  ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html):
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    lines = [line.strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
