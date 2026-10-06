"""Save a copy of every sent mail into the mailbox's "Sent" folder over IMAP, the same
way desktop mail clients do. SMTP alone leaves no trace in the mailbox."""

import base64
import imaplib
import re
import ssl
import time
from dataclasses import dataclass

from .errors import SendError
from .sender import _network_hint

imaplib.Commands.setdefault("XLIST", ("AUTH", "SELECTED"))
imaplib.Commands.setdefault("ID", ("NONAUTH", "AUTH", "SELECTED"))

SENT_NAMES = ("sent", "sent items", "sent messages", "sent mail", "已发送", "已发送邮件", "已发邮件")
_LIST_RE = re.compile(r'^\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.*)$')


@dataclass(frozen=True)
class ImapConfig:
    host: str
    port: int
    security: str  # ssl | starttls | none
    username: str
    password: str
    timeout: float = 30.0


def decode_mutf7(name):
    """IMAP mailbox names use modified UTF-7: '&XfJT0ZAB-' -> '已发送'."""
    out, i = [], 0
    while i < len(name):
        if name[i] == "&":
            j = name.find("-", i)
            if j < 0:
                return name
            if j == i + 1:
                out.append("&")
            else:
                chunk = name[i + 1 : j].replace(",", "/")
                try:
                    out.append(base64.b64decode(chunk + "=" * (-len(chunk) % 4)).decode("utf-16-be"))
                except (ValueError, UnicodeDecodeError):
                    return name
            i = j + 1
        else:
            out.append(name[i])
            i += 1
    return "".join(out)


def _context(weak):
    ctx = ssl.create_default_context()
    if weak:
        # Some servers (263 among them) still negotiate 1024-bit DH, which OpenSSL 3
        # rejects by default. Still an encrypted connection, unlike plain port 143.
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    return ctx


class SentFolder:
    """Use as a context manager; connects lazily on the first save(). After a
    connection/login failure every later save() fails fast with the same message,
    so a batch never waits on a dead server once per mail."""

    def __init__(self, config):
        self._cfg = config
        self._conn = None
        self._broken = ""
        self.folder = None  # quoted raw name, as used in IMAP commands
        self.folder_name = ""  # human-readable name

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- connection -------------------------------------------------------------
    def _open(self, weak):
        cfg = self._cfg
        if cfg.security == "ssl":
            return imaplib.IMAP4_SSL(cfg.host, cfg.port, ssl_context=_context(weak), timeout=cfg.timeout)
        conn = imaplib.IMAP4(cfg.host, cfg.port, timeout=cfg.timeout)
        if cfg.security == "starttls":
            try:
                conn.starttls(ssl_context=_context(weak))
            except Exception:
                conn.shutdown()
                raise
        return conn

    def connect(self):
        cfg = self._cfg
        try:
            try:
                conn = self._open(weak=False)
            except ssl.SSLError as e:
                if "dh key too small" not in str(e).lower():
                    raise
                conn = self._open(weak=True)
            conn.login(cfg.username, cfg.password)
        except imaplib.IMAP4.error as e:
            self._broken = f"IMAP 登录失败（请确认密码/授权码，并已在邮箱设置中开启 IMAP）: {_text(e)}"
            raise SendError(self._broken)
        except OSError as e:
            self._broken = f"无法连接 IMAP 服务器 {cfg.host}:{cfg.port}: {e}{_network_hint(cfg.host)}"
            raise SendError(self._broken)
        self._conn = conn
        if "ID" in conn.capabilities:  # 163/126 refuse APPEND from clients that don't identify
            try:
                conn._simple_command("ID", '("name" "mailer" "version" "1.0")')
            except imaplib.IMAP4.error:
                pass
        if self.folder is None:
            self.folder, self.folder_name = self._find_sent()
        return self.folder_name

    def close(self):
        if self._conn is not None:
            try:
                self._conn.logout()
            except (imaplib.IMAP4.error, OSError):
                pass
            self._conn = None

    # -- folder discovery ---------------------------------------------------------
    def _list(self, command):
        try:
            typ, dat = self._conn._simple_command(command, '""', '"*"')
            typ, dat = self._conn._untagged_response(typ, dat, command)
        except imaplib.IMAP4.error:
            return []
        entries = []
        for item in dat or []:
            if item is None:
                continue
            literal = None
            if isinstance(item, tuple):  # name sent as an IMAP literal
                line, literal = _text(item[0]), _text(item[1])
            else:
                line = _text(item)
            m = _LIST_RE.match(line.strip())
            if not m:
                continue
            raw = literal if literal is not None else m["name"].strip()
            unquoted = raw[1:-1].replace('\\"', '"') if raw.startswith('"') and raw.endswith('"') else raw
            quoted = '"' + unquoted.replace("\\", "\\\\").replace('"', '\\"') + '"'
            entries.append((m["flags"].lower().split(), quoted, decode_mutf7(unquoted)))
        return entries

    def _find_sent(self):
        entries = (self._list("XLIST") if "XLIST" in self._conn.capabilities else []) or self._list("LIST")
        for flags, quoted, name in entries:
            if "\\sent" in flags:
                return quoted, name
        for flags, quoted, name in entries:
            leaf = re.split(r"[/.]", name)[-1].strip().lower()
            if leaf in SENT_NAMES:
                return quoted, name
        names = "、".join(name for _, _, name in entries) or "（无）"
        self._broken = f"在邮箱里找不到“已发送”文件夹。现有文件夹: {names}"
        raise SendError(self._broken)

    # -- saving -------------------------------------------------------------------
    def save(self, message):
        if self._broken:
            raise SendError(self._broken)
        data = message.as_bytes(policy=message.policy.clone(linesep="\r\n"))
        for attempt in (1, 2):
            try:
                if self._conn is None:
                    self.connect()
                typ, resp = self._conn.append(self.folder, r"(\Seen)", imaplib.Time2Internaldate(time.time()), data)
                if typ != "OK":
                    raise SendError(f"服务器拒绝存入“{self.folder_name}”: {_text(resp[0]) if resp else typ}")
                return
            except (imaplib.IMAP4.abort, OSError) as e:  # connection dropped; reconnect once
                self._conn = None
                if attempt == 2:
                    raise SendError(f"存入已发送失败: {e}")
            except imaplib.IMAP4.error as e:
                raise SendError(f"存入已发送失败: {_text(e)}")


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
