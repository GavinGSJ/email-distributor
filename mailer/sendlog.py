"""Append-only JSONL record of what was sent, so a re-run after a failure does not
mail the same recipient twice."""

import json
from datetime import datetime
from pathlib import Path


class SendLog:
    def __init__(self, path):
        self.path = Path(path)
        self._sent = set()
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("status") == "sent":
                    self._sent.add((entry["to"], entry["subject"]))

    def was_sent(self, to, subject):
        return (to, subject) in self._sent

    def record(self, to, subject, status, error=""):
        entry = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "to": to,
            "subject": subject,
            "status": status,
            "error": error,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if status == "sent":
            self._sent.add((to, subject))
