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


# -- rich-text bodies written in the web editor --------------------------------
_KEEP_TAGS = {
    "p", "div", "br", "span", "b", "strong", "i", "em", "u", "s", "strike", "sub", "sup",
    "ul", "ol", "li", "blockquote", "a", "h1", "h2", "h3", "hr",
}
_DROP_WITH_CONTENT = {"script", "style", "head", "title", "iframe", "object", "embed", "template", "svg", "math"}
_VOID = {"br", "hr"}
_KEEP_STYLES = {
    "color", "background-color", "font-size", "font-family", "font-weight", "font-style",
    "text-decoration", "text-align", "line-height", "margin-left", "padding-left",
}
_SAFE_STYLE_VALUE = re.compile(r"^[\w\s#%.,\"'()+-]+$")
_SAFE_HREF = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)
_VAR_WITH_TAGS = re.compile(r"\{\{((?:[^{}<]|<[^>]*>)*)\}\}")


def _clean_style(style):
    kept = []
    for decl in style.split(";"):
        name, _, value = decl.partition(":")
        name, value = name.strip().lower(), value.strip()
        if name in _KEEP_STYLES and _SAFE_STYLE_VALUE.match(value) and "url(" not in value.lower():
            kept.append(f"{name}:{value}")
    return ";".join(kept)


class _Sanitizer(HTMLParser):
    """Whitelist filter for editor HTML: keeps basic formatting, drops scripts,
    event handlers, images and anything else it does not know."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._open = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _DROP_WITH_CONTENT:
            self._skip += 1
            return
        if self._skip or tag not in _KEEP_TAGS:
            if tag == "font" and not self._skip:  # legacy <font color/face> -> span
                self._start_font(dict(attrs))
            return
        attrs = dict(attrs)
        parts = [f'<{tag}']
        style = _clean_style(attrs.get("style") or "")
        if style:
            parts.append(f' style="{escape(style, quote=True)}"')
        if tag == "a":
            href = (attrs.get("href") or "").strip()
            if _SAFE_HREF.match(href):
                parts.append(f' href="{escape(href, quote=True)}"')
        self.out.append("".join(parts) + ">")
        if tag not in _VOID:
            self._open.append(tag)

    def _start_font(self, attrs):
        style = []
        if attrs.get("color"):
            style.append(f"color:{attrs['color']}")
        if attrs.get("face"):
            style.append(f"font-family:{attrs['face']}")
        style = _clean_style(";".join(style))
        self.out.append(f'<span style="{escape(style, quote=True)}">' if style else "<span>")
        self._open.append("span")

    def handle_startendtag(self, tag, attrs):
        if tag in _VOID:
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _DROP_WITH_CONTENT:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "font":
            tag = "span"
        if tag in self._open:  # close anything left open inside it first
            while self._open:
                top = self._open.pop()
                self.out.append(f"</{top}>")
                if top == tag:
                    break

    def handle_data(self, data):
        if not self._skip:
            self.out.append(escape(data, quote=False))

    def result(self):
        while self._open:
            self.out.append(f"</{self._open.pop()}>")
        return "".join(self.out)


def sanitize_html(html):
    parser = _Sanitizer()
    parser.feed(html)
    parser.close()
    return parser.result()


def clean_editor_html(html):
    """Sanitize editor HTML, and make {{variable}} survive formatting that was applied
    to only part of it (e.g. {{项目<b>名称</b>}}) so it is still recognised."""
    html = sanitize_html(html)
    return _VAR_WITH_TAGS.sub(lambda m: "{{" + re.sub(r"<[^>]*>", "", m.group(1)) + "}}", html)
