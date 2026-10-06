import mimetypes
from pathlib import Path

from .errors import AttachmentError
from .models import Attachment

DEFAULT_MAX_TOTAL_MB = 20  # most mail servers reject anything near 25MB


def load_attachments(paths, max_total_mb=DEFAULT_MAX_TOTAL_MB):
    """Read every file once so the same bytes can be reused for each mail.
    A directory means "every file directly inside it"."""
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(f for f in p.iterdir() if f.is_file()))
        elif p.is_file():
            files.append(p)
        else:
            raise AttachmentError(f"找不到附件: {p}")

    attachments = []
    total = 0
    for f in files:
        data = f.read_bytes()
        total += len(data)
        mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        maintype, _, subtype = mime.partition("/")
        if maintype == "text":
            # A text/* part needs a charset we can't know (.csv from Excel is often GBK);
            # sending raw bytes as a binary attachment keeps the file byte-identical.
            maintype, subtype = "application", "octet-stream"
        attachments.append(Attachment(f.name, maintype, subtype, data))

    if total > max_total_mb * 1024 * 1024:
        raise AttachmentError(
            f"附件总大小 {total / 1024 / 1024:.1f}MB，超过上限 {max_total_mb}MB"
        )
    return attachments
