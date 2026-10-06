"""Signature loading. A signature is an .html file (images referenced by relative
path are embedded inline) or a plain .txt file."""

import mimetypes
import re
from email.utils import make_msgid
from html import escape
from pathlib import Path

from .body import HTML_SUFFIXES, html_to_text, text_to_html
from .errors import SignatureError
from .models import InlineImage, Signature

_IMG_SRC = re.compile(r"""(<img\b[^>]*?\bsrc\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)
_EXTERNAL = ("http://", "https://", "cid:", "data:")


def build_signature(text, logo_path=None):
    """Signature from plain text plus an optional logo image shown under it."""
    text = (text or "").strip()
    if not text and not logo_path:
        return None
    html = "<br>".join(escape(line) for line in text.splitlines())
    images = ()
    if logo_path:
        logo_path = Path(logo_path)
        mime = mimetypes.guess_type(logo_path.name)[0] or "image/png"
        maintype, _, subtype = mime.partition("/")
        img = InlineImage(make_msgid(domain="signature.local")[1:-1], maintype, subtype, logo_path.read_bytes())
        html += f'<br><img src="cid:{img.cid}" alt="logo" style="max-height:80px;margin-top:6px">'
        images = (img,)
    html = f'<div style="margin-top:20px;color:#555;font-size:13px;line-height:1.6">{html}</div>'
    return Signature(html=html, text=text, images=images)


def load_signature(path):
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise SignatureError(f"找不到签名文件: {path}")
    except UnicodeDecodeError:
        raise SignatureError(f"签名文件需要是 UTF-8 编码: {path}")

    if path.suffix.lower() not in HTML_SUFFIXES:
        return Signature(html=text_to_html(raw), text=raw.strip())

    images = {}  # resolved path -> InlineImage

    def embed(match):
        prefix, quote, src = match.groups()
        if src.lower().startswith(_EXTERNAL):
            return match.group(0)
        image_path = (path.parent / src).resolve()
        if image_path not in images:
            if not image_path.is_file():
                raise SignatureError(f"签名里引用的图片不存在: {src} (在 {path.name} 中)")
            mime = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
            maintype, _, subtype = mime.partition("/")
            images[image_path] = InlineImage(
                cid=make_msgid(domain="signature.local")[1:-1],
                maintype=maintype,
                subtype=subtype,
                data=image_path.read_bytes(),
            )
        return f"{prefix}{quote}cid:{images[image_path].cid}{quote}"

    html = _IMG_SRC.sub(embed, raw)
    return Signature(html=html, text=html_to_text(html), images=tuple(images.values()))
