"""Local record of every send batch, for tracing what was sent to whom.

userdata/history/<batch id>/
  batch.json        who/when/what, plus each mail's status
  001.eml ...       each mail exactly as sent, minus attachment payloads
  attachments/      the attachments, stored once per batch

Attachments are kept once instead of inside every .eml: 20 suppliers x a 10MB
quotation pack would otherwise mean 200MB per batch. The full copies (with
attachments) live in the mailbox's Sent folder."""

import copy
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .errors import ConfigError

_BATCH_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")


class SendArchive:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # -- writing --------------------------------------------------------------
    def create(self, *, sender, subject, cc, bcc, variables, attachment_paths, mails):
        """Record a batch before sending. `mails` are PreparedMail objects; every
        entry starts as "pending" and is updated by update()."""
        batch_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
        folder = self.root / batch_id
        (folder / "attachments").mkdir(parents=True)
        attachments = []
        for path in attachment_paths:
            path = Path(path)
            name, i = path.name, 1
            while (folder / "attachments" / name).exists():  # two uploads with the same file name
                i += 1
                name = f"{path.stem} ({i}){path.suffix}"
            shutil.copy2(path, folder / "attachments" / name)
            attachments.append({"name": name, "size": path.stat().st_size})
        entries = []
        for n, mail in enumerate(mails, start=1):
            eml = f"{n:03d}.eml"
            (folder / eml).write_bytes(bytes(_without_attachments(mail.message)))
            entries.append({
                "n": n,
                "to": mail.to,
                "subject": mail.subject,
                "fields": dict(mail.recipient.fields),
                "eml": eml,
                "status": "pending",
                "error": "",
                "archived": "",
                "time": "",
            })
        batch = {
            "id": batch_id,
            "time": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "from": sender,
            "subject": subject,
            "cc": cc,
            "bcc": bcc,
            "variables": variables,
            "attachments": attachments,
            "mails": entries,
        }
        self._write(batch_id, batch)
        return batch_id

    def update(self, batch_id, n, result):
        with self._lock:
            batch = self.get(batch_id)
            entry = batch["mails"][n - 1]
            entry.update(status=result.status, error=result.error, archived=result.archived,
                         time=f"{datetime.now():%Y-%m-%d %H:%M:%S}")
            self._write(batch_id, batch)

    def _write(self, batch_id, batch):
        path = self.root / batch_id / "batch.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # -- reading --------------------------------------------------------------
    def get(self, batch_id):
        if not _BATCH_ID.match(str(batch_id)):
            raise ConfigError("无效的记录编号")
        try:
            return json.loads((self.root / batch_id / "batch.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ConfigError("找不到这条发送记录")

    def summaries(self):
        """Newest first; enough to list and search batches without opening each mail."""
        out = []
        for folder in sorted(self.root.iterdir(), reverse=True):
            if not folder.is_dir() or not _BATCH_ID.match(folder.name):
                continue
            try:
                b = self.get(folder.name)
            except (ConfigError, ValueError):
                continue
            mails = b["mails"]
            out.append({
                "id": b["id"],
                "time": b["time"],
                "subject": b["subject"],
                "first_subject": mails[0]["subject"] if mails else "",
                "cc": b["cc"],
                "total": len(mails),
                "sent": sum(m["status"] == "sent" for m in mails),
                "failed": sum(m["status"] == "failed" for m in mails),
                "archived": sum(m["archived"] == "saved" for m in mails),
                "attachments": [a["name"] for a in b["attachments"]],
                # flat text for the search box
                "search": " ".join([b["subject"], b["cc"], *(m["to"] + " " + m["subject"] + " " + " ".join(m["fields"].values()) for m in mails)]).lower(),
            })
        return out

    def mail_path(self, batch_id, n):
        batch = self.get(batch_id)
        try:
            return self.root / batch_id / batch["mails"][int(n) - 1]["eml"]
        except (IndexError, ValueError):
            raise ConfigError("找不到这封邮件")

    def attachment_path(self, batch_id, name):
        batch = self.get(batch_id)
        if name not in {a["name"] for a in batch["attachments"]}:
            raise ConfigError("找不到这个附件")
        return self.root / batch_id / "attachments" / name


def _without_attachments(message):
    msg = copy.deepcopy(message)
    if msg.get_content_type() == "multipart/mixed":
        names = [p.get_filename() for p in msg.iter_attachments()]
        msg.set_payload([p for p in msg.get_payload() if p.get_content_disposition() != "attachment"])
        if names:
            msg["X-Mailer-Attachments"] = "; ".join(names)
    return msg
