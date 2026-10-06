"""Everything the UI remembers between runs, as JSON files in one folder:
  account.json    sender address, SMTP/IMAP servers, encrypted password
  signature.json  signature text + logo upload id
  draft.json      the mail being written (autosaved)
  templates.json  named, reusable copies of a draft
  recipient_lists.json  named recipient lists (address book)
  email_groups.json     named cc/bcc address groups
  uploads/        attachments and logos the user picked
  history/        one folder per send batch (see archive.py)"""

import json
import os
import re
import uuid
from pathlib import Path

from . import providers, secret
from .config import SmtpConfig
from .errors import ConfigError
from .sentbox import ImapConfig
from .signature import build_signature

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "userdata"
_ID = re.compile(r"^[0-9a-f]{32}$")
_BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_LIBRARY = {
    "templates": ("templates.json", "模板"),
    "lists": ("recipient_lists.json", "名单"),
    "groups": ("email_groups.json", "组合"),
}


class Store:
    def __init__(self, root=DEFAULT_ROOT):
        self.root = Path(root)
        self.uploads = self.root / "uploads"
        self.uploads.mkdir(parents=True, exist_ok=True)
        self.history_dir = self.root / "history"

    # -- json helpers ---------------------------------------------------------
    def _read(self, name, default):
        try:
            return json.loads((self.root / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return default

    def _write(self, name, data):
        path = self.root / name
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # -- account --------------------------------------------------------------
    def account(self):
        """Public part of the account (never includes the password)."""
        acc = self._read("account.json", None)
        if not acc:
            return None
        public = {k: v for k, v in acc.items() if k != "password"}
        public["has_password"] = bool(acc.get("password"))
        if "imap_host" not in public:  # saved before IMAP support: fill in from the presets
            guess = providers.imap_for_smtp(public.get("host", ""))
            public["save_sent"] = guess is not None
            public["imap_host"], public["imap_port"], public["imap_security"] = guess or ("", 993, "ssl")
        return public

    def save_account(self, email, name, host, port, security, password,
                     save_sent=False, imap_host="", imap_port=993, imap_security="ssl"):
        self._write(
            "account.json",
            {
                "email": email,
                "name": name,
                "host": host,
                "port": int(port),
                "security": security,
                "save_sent": bool(save_sent),
                "imap_host": imap_host,
                "imap_port": int(imap_port),
                "imap_security": imap_security,
                "password": secret.encrypt(password) if password else "",
            },
        )

    def stored_password(self):
        acc = self._read("account.json", None) or {}
        try:
            return secret.decrypt(acc.get("password", ""))
        except (OSError, ValueError):
            return ""

    def smtp_config(self):
        acc = self.account()
        if not acc:
            raise ConfigError("还没有设置发件账号")
        return smtp_config_for(acc, self.stored_password())

    def imap_config(self):
        """IMAP settings for filing sent mail, or None when that is switched off."""
        acc = self.account()
        if not acc or not acc.get("save_sent") or not acc.get("imap_host"):
            return None
        return imap_config_for(acc, self.stored_password())

    # -- signature ------------------------------------------------------------
    def signature_settings(self):
        return self._read("signature.json", {"text": "", "logo": None})

    def save_signature_settings(self, text, logo):
        if logo and not self.upload_path(logo["id"]).is_file():
            raise ConfigError("Logo 图片不存在，请重新上传")
        self._write("signature.json", {"text": text, "logo": logo})

    def signature(self):
        s = self.signature_settings()
        logo = self.upload_path(s["logo"]["id"]) if s.get("logo") else None
        if logo is not None and not logo.is_file():
            logo = None
        return build_signature(s.get("text", ""), logo)

    # -- draft & templates ----------------------------------------------------
    def draft(self):
        return self._read("draft.json", None)

    def save_draft(self, compose):
        self._write("draft.json", compose)

    def templates(self):
        return self._read("templates.json", {})

    def save_template(self, name, compose):
        name = name.strip()
        if not name:
            raise ConfigError("模板名称不能为空")
        data = self.templates()
        data[name] = compose
        self._write("templates.json", data)

    def delete_template(self, name):
        data = self.templates()
        data.pop(name, None)
        self._write("templates.json", data)

    # -- saved recipient lists (address book) ---------------------------------
    def recipient_lists(self):
        return self._read("recipient_lists.json", {})

    def save_recipient_list(self, name, columns, rows):
        name = name.strip()
        if not name:
            raise ConfigError("名单名称不能为空")
        if not rows:
            raise ConfigError("名单里没有收件人")
        data = self.recipient_lists()
        data[name] = {"columns": columns, "rows": rows}
        self._write("recipient_lists.json", data)

    def delete_recipient_list(self, name):
        data = self.recipient_lists()
        data.pop(name, None)
        self._write("recipient_lists.json", data)

    # -- cc/bcc address groups ------------------------------------------------
    def email_groups(self):
        return self._read("email_groups.json", {})

    def save_email_group(self, name, emails):
        name = name.strip()
        if not name:
            raise ConfigError("组合名称不能为空")
        if not emails:
            raise ConfigError("组合里至少要有一个邮箱")
        data = self.email_groups()
        data[name] = list(emails)
        self._write("email_groups.json", data)

    def delete_email_group(self, name):
        data = self.email_groups()
        data.pop(name, None)
        self._write("email_groups.json", data)

    def rename(self, kind, old, new):
        """Rename a template / recipient list / email group; returns that collection."""
        file, what = _LIBRARY.get(kind, (None, None))
        if not file:
            raise ConfigError("未知的资料类型")
        new = (new or "").strip()
        if not new:
            raise ConfigError(f"{what}名称不能为空")
        data = self._read(file, {})
        if old not in data:
            raise ConfigError(f"找不到{what}“{old}”，可能已被删除")
        if new != old:
            if new in data:
                raise ConfigError(f"已经有名为“{new}”的{what}了")
            data = {(new if k == old else k): v for k, v in data.items()}
            self._write(file, data)
        return data

    # -- uploads --------------------------------------------------------------
    def save_upload(self, filename, data):
        filename = _BAD_NAME.sub("_", Path(filename).name).strip() or "file"
        upload_id = uuid.uuid4().hex
        folder = self.uploads / upload_id
        folder.mkdir()
        (folder / filename).write_bytes(data)
        return {"id": upload_id, "name": filename, "size": len(data)}

    def upload_path(self, upload_id):
        """Path of the file stored under `upload_id` (the folder holds exactly one file)."""
        if not _ID.match(str(upload_id)):
            raise ConfigError("无效的文件编号")
        folder = self.uploads / upload_id
        files = list(folder.iterdir()) if folder.is_dir() else []
        return files[0] if files else folder / "missing"


def imap_config_for(acc, password):
    return ImapConfig(
        host=acc["imap_host"],
        port=int(acc["imap_port"]),
        security=acc["imap_security"],
        username=acc["email"],
        password=password,
    )


def smtp_config_for(acc, password):
    return SmtpConfig(
        host=acc["host"],
        port=int(acc["port"]),
        security=acc["security"],
        username=acc["email"],
        password=password,
        from_addr=acc["email"],
        from_name=acc.get("name", ""),
        timeout=30,
    )
