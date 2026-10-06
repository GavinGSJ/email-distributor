"""Loading of the two TOML config files:
  smtp.toml  - how to send (server, account). Kept out of version control.
  job.toml   - what to send (subject, body, cc, attachments, recipients)."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

_DEFAULT_PORTS = {"ssl": 465, "starttls": 587, "none": 25}
DEFAULT_PASSWORD_ENV = "MAILER_SMTP_PASSWORD"


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    security: str
    username: str
    password: str  # may be "" -> caller should ask the user
    from_addr: str
    from_name: str = ""
    timeout: float = 30.0


@dataclass(frozen=True)
class JobConfig:
    subject: str
    body: Path
    recipients: Path
    sheet: str = None
    signature: Path = None
    cc: tuple = ()
    bcc: tuple = ()
    reply_to: str = None
    attachments: tuple = ()
    variables: dict = field(default_factory=dict)
    interval_seconds: float = 2.0
    max_attachment_mb: int = 20
    path: Path = None  # the job file itself


def _read_toml(path):
    path = Path(path)
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"找不到配置文件: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} 不是合法的 TOML: {e}")


def _require(section, key, where):
    value = section.get(key)
    if value in (None, ""):
        raise ConfigError(f"{where} 缺少必填项 {key!r}")
    return value


def load_smtp(path):
    section = _read_toml(path).get("smtp")
    if not isinstance(section, dict):
        raise ConfigError(f"{path} 里需要有 [smtp] 段")
    security = section.get("security", "ssl").lower()
    if security not in _DEFAULT_PORTS:
        raise ConfigError(f"security 只能是 ssl / starttls / none，当前是 {security!r}")
    username = section.get("username", "")
    password = section.get("password") or os.environ.get(
        section.get("password_env", DEFAULT_PASSWORD_ENV), ""
    )
    from_addr = section.get("from_addr") or username
    if not from_addr:
        raise ConfigError("[smtp] 需要 from_addr 或 username 来确定发件人")
    return SmtpConfig(
        host=_require(section, "host", "[smtp]"),
        port=int(section.get("port", _DEFAULT_PORTS[security])),
        security=security,
        username=username,
        password=password,
        from_addr=from_addr,
        from_name=section.get("from_name", ""),
        timeout=float(section.get("timeout", 30)),
    )


def _as_list(value):
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    return tuple(v.strip() for v in value if v.strip())


def load_job(path):
    path = Path(path).resolve()
    data = _read_toml(path)
    mail = data.get("mail") or {}
    rec = data.get("recipients") or {}
    send = data.get("send") or {}
    base = path.parent

    def rel(p):
        return (base / p).resolve()

    return JobConfig(
        subject=_require(mail, "subject", "[mail]"),
        body=rel(_require(mail, "body", "[mail]")),
        recipients=rel(_require(rec, "file", "[recipients]")),
        sheet=rec.get("sheet") or None,
        signature=rel(mail["signature"]) if mail.get("signature") else None,
        cc=_as_list(mail.get("cc")),
        bcc=_as_list(mail.get("bcc")),
        reply_to=mail.get("reply_to") or None,
        attachments=tuple(rel(a) for a in _as_list(mail.get("attachments"))),
        variables={k: str(v) for k, v in (data.get("variables") or {}).items()},
        interval_seconds=float(send.get("interval_seconds", 2)),
        max_attachment_mb=int(send.get("max_attachment_mb", 20)),
        path=path,
    )
