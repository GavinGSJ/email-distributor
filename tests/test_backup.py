import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from mailer import backup
from mailer.errors import ConfigError
from mailer.store import Store

LOGO_ID = "a" * 32
ATT_ID = "b" * 32
STALE_ID = "c" * 32  # an old upload nothing refers to any more


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.src = self.tmp / "src"
        self.store = Store(self.src)
        self.store.save_account(email="me@wison.com", name="桂", host="smtp.263.net", port=465, security="ssl",
                                password="secret-code", save_sent=True, imap_host="imap.263.net", imap_port=143,
                                imap_security="starttls")
        for uid, name, data in [(LOGO_ID, "logo.png", b"png"), (ATT_ID, "报价单.xlsx", b"xlsx"), (STALE_ID, "old.pdf", b"old")]:
            (self.src / "uploads" / uid).mkdir(parents=True)
            (self.src / "uploads" / uid / name).write_bytes(data)
        self.store.save_signature_settings("桂\n采购部", {"id": LOGO_ID, "name": "logo.png"})
        self.store.save_template("球阀询价", {"subject": "询价", "body": "b", "attachments": [{"id": ATT_ID, "name": "报价单.xlsx"}]})
        self.store.save_recipient_list("阀门", ["邮箱"], [{"邮箱": "a@x.com"}])
        self.store.save_email_group("抄送组", ["boss@wison.com"])
        self.store.save_draft({"subject": "草稿", "body": "x", "attachments": []})
        batch = self.src / "history" / "20261004-120000-abcd"
        (batch / "attachments").mkdir(parents=True)
        (batch / "batch.json").write_text('{"id": "20261004-120000-abcd"}', encoding="utf-8")
        (batch / "001.eml").write_bytes(b"Subject: x\r\n\r\nbody")
        (batch / "attachments" / "报价单.xlsx").write_bytes(b"xlsx")

    def export(self, items=None):
        dest = self.tmp / "out.zip"
        return dest, backup.export(self.src, dest, items)

    def test_export_contents(self):
        dest, manifest = self.export()
        self.assertEqual(manifest["account"], "me@wison.com")
        self.assertEqual(manifest["items"]["templates"]["count"], 1)
        self.assertEqual(manifest["items"]["history"]["count"], 1)
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
            account = json.loads(z.read("data/account.json"))
        self.assertNotIn("password", account)  # never exported
        self.assertEqual(account["imap_host"], "imap.263.net")
        self.assertIn(f"data/uploads/{LOGO_ID}/logo.png", names)
        self.assertIn(f"data/uploads/{ATT_ID}/报价单.xlsx", names)
        self.assertNotIn(f"data/uploads/{STALE_ID}/old.pdf", names)  # unreferenced uploads stay behind
        self.assertIn("data/history/20261004-120000-abcd/attachments/报价单.xlsx", names)

    def test_partial_export_for_sharing(self):
        dest, manifest = self.export(["templates", "lists"])
        self.assertEqual(set(manifest["items"]), {"templates", "lists"})
        with zipfile.ZipFile(dest) as z:
            names = set(z.namelist())
        self.assertNotIn("data/account.json", names)
        self.assertIn(f"data/uploads/{ATT_ID}/报价单.xlsx", names)  # the template's attachment comes along
        self.assertNotIn(f"data/uploads/{LOGO_ID}/logo.png", names)

    def test_restore_on_new_computer(self):
        dest, _ = self.export()
        new = self.tmp / "new"
        summary = backup.restore(new, dest, list(backup.ITEMS), mode="merge")
        restored = Store(new)
        self.assertEqual(restored.account()["email"], "me@wison.com")
        self.assertEqual(restored.stored_password(), "")  # must be typed again
        self.assertIn("重新输入密码", summary["account"])
        self.assertEqual(restored.signature_settings()["text"], "桂\n采购部")
        self.assertTrue(restored.signature().images)  # logo file came back
        self.assertEqual(list(restored.templates()), ["球阀询价"])
        self.assertTrue(restored.upload_path(ATT_ID).is_file())
        self.assertEqual(restored.email_groups(), {"抄送组": ["boss@wison.com"]})
        self.assertTrue((new / "history" / "20261004-120000-abcd" / "001.eml").is_file())

    def test_merge_keeps_existing_and_renames_conflicts(self):
        dest, _ = self.export()
        self.store.save_template("球阀询价", {"subject": "本地改过的", "body": "b", "attachments": []})
        self.store.save_email_group("抄送组", ["boss@wison.com"])  # identical -> not duplicated
        summary = backup.restore(self.src, dest, ["templates", "groups", "account", "history"], mode="merge")
        templates = self.store.templates()
        self.assertEqual(templates["球阀询价"]["subject"], "本地改过的")
        self.assertEqual(templates["球阀询价（导入）"]["subject"], "询价")
        self.assertEqual(list(self.store.email_groups()), ["抄送组"])
        self.assertEqual(self.store.stored_password(), "secret-code")  # untouched
        self.assertIn("已存在 1 批", summary["history"])

    def test_replace_keeps_password_of_same_account(self):
        dest, _ = self.export()
        self.store.save_template("另一个", {"subject": "s"})
        backup.restore(self.src, dest, ["templates", "account"], mode="replace")
        self.assertEqual(list(self.store.templates()), ["球阀询价"])
        self.assertEqual(self.store.stored_password(), "secret-code")

    def test_rejects_non_backups_and_ignores_foreign_paths(self):
        junk = self.tmp / "junk.zip"
        junk.write_bytes(b"not a zip")
        with self.assertRaises(ConfigError):
            backup.inspect(junk)
        evil = self.tmp / "evil.zip"
        with zipfile.ZipFile(evil, "w") as z:
            z.writestr("backup.json", json.dumps({"format": backup.FORMAT, "version": 1, "created": "", "items": {}}))
            z.writestr("data/history/20261004-120000-abcd/../../../../escape.txt", "x")
            z.writestr("data/uploads/notanid/x.txt", "x")
            z.writestr("data/history/20261005-120000-abcd/batch.json", "{}")
        new = self.tmp / "new"
        backup.restore(new, evil, ["history"], mode="merge")
        self.assertFalse((self.tmp / "escape.txt").exists())
        self.assertEqual([p.name for p in (new / "history").iterdir()], ["20261005-120000-abcd"])

    def test_backup_folder(self):
        folder = backup.BackupFolder(self.tmp / "backups", self.src)
        made = folder.export(["templates"])
        self.assertTrue(made["name"].startswith("邮件助手备份_"))
        self.assertIsNotNone(folder.daily_snapshot())
        self.assertIsNone(folder.daily_snapshot())  # once a day
        for i in range(9):
            folder.snapshot(f"恢复前{i}")
        self.assertEqual(len(list(folder.auto.glob("*.zip"))), backup.BackupFolder.KEEP_AUTO)
        kinds = {f["kind"] for f in folder.listing()}
        self.assertEqual(kinds, {"manual", "auto"})
        with self.assertRaises(ConfigError):
            folder.resolve("../src/account.json")
        with self.assertRaises(ConfigError):
            folder.save_uploaded("x.txt", b"hello")
        empty = backup.BackupFolder(self.tmp / "b2", self.tmp / "nothing-here")
        self.assertIsNone(empty.daily_snapshot())  # nothing to back up yet


if __name__ == "__main__":
    unittest.main()
