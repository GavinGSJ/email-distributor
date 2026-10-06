from dataclasses import dataclass, field


@dataclass(frozen=True)
class Recipient:
    """One outgoing mail. `emails` all go into the To header of that single mail;
    `fields` are every column of the source row, usable as {{变量}} in templates."""

    emails: tuple
    fields: dict = field(default_factory=dict)
    source: str = ""  # e.g. "第 3 行", for error messages


@dataclass(frozen=True)
class Attachment:
    filename: str
    maintype: str
    subtype: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class InlineImage:
    cid: str
    maintype: str
    subtype: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class Signature:
    html: str
    text: str
    images: tuple = ()
