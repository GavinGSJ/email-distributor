"""Drives the web UI's HTTP API end to end against a fake SMTP server."""

import email
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from email import policy
from http.server import ThreadingHTTPServer
from pathlib import Path

from mailer import secret
from mailer.store import Store
from mailer.webapp import App, _free_port, make_handler

from fakeimap import FakeImapServer
from fakesmtp import FakeSmtpServer

LOGO = (Path(__file__).resolve().parent.parent / "signatures" / "logo.png").read_bytes()


class ApiTestCase(unittest.TestCase):
    """Real HTTP server + fake SMTP, with helpers; holds no tests itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.smtp = FakeSmtpServer()
        self.store = Store(self.tmp)
        self.app = App(self.store, backup_dir=self.tmp / "backups")
        port = _free_port(18765)
        self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.app, port))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.server.shutdown()
        self.server.server_close()
        self.smtp.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, path, body=None, raw_name=None, token=None, host=None):
        headers = {"X-Token": token if token is not None else self.app.token}
        if host:
            headers["Host"] = host
        data = None
        if raw_name is not None:
            path += "?name=" + urllib.parse.quote(raw_name)
            data = body
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read())

    def login(self):
        return self.call("/api/account", {
            "email": "me@example.com", "name": "赵采购", "password": "secret-code",
            "host": "127.0.0.1", "port": self.smtp.port, "security": "none",
        })

    def compose(self, **over):
        c = {
            "subject": "【询价】{{公司}}",
            "body": "{{联系人}}，您好：\n\n请报价。",
            "cc": "boss@example.com",
            "bcc": "",
            "columns": ["邮箱", "公司", "联系人"],
            "rows": [
                {"邮箱": "a@example.com", "公司": "甲公司", "联系人": "张经理"},
                {"邮箱": "b@example.com; b2@example.com", "公司": "乙公司", "联系人": "李总"},
                {},  # blank row left in the table is ignored
            ],
            "attachments": [],
            "use_signature": True,
        }
        c.update(over)
        return c

    def download(self, path, token=None):
        url = self.base + path.replace("报价单.xlsx", urllib.parse.quote("报价单.xlsx"))
        url += "&token=" + urllib.parse.quote(token or self.app.token)
        with urllib.request.urlopen(url) as res:
            return res.read()

    def wait(self, job):
        for _ in range(100):
            status, st = self.call(f"/api/send/status?job={job}")
            if st["done"]:
                return st
            time.sleep(0.1)
        self.fail("send job did not finish")

    def app_interval(self, seconds):
        import mailer.webapp as w

        old = w.SEND_INTERVAL
        w.SEND_INTERVAL = seconds
        self.addCleanup(setattr, w, "SEND_INTERVAL", old)


class WebApiTests(ApiTestCase):
    def test_guards(self):
        self.assertEqual(self.call("/api/state", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/state", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("/api/state")[0], 200)

    def test_compose_requires_account(self):
        status, res = self.call("/api/preview", self.compose())
        self.assertEqual(status, 400)
        self.assertIn("账号", res["error"])

    def test_account_saved_encrypted_and_password_kept_when_blank(self):
        status, acc = self.login()
        self.assertEqual(status, 200, acc)
        self.assertTrue(acc["has_password"])
        self.assertNotIn("password", acc)
        raw = (self.tmp / "account.json").read_text(encoding="utf-8")
        self.assertNotIn("secret-code", raw)
        if sys.platform == "win32":
            self.assertIn('"dpapi:', raw)
        self.assertEqual(self.store.stored_password(), "secret-code")

        status, _ = self.call("/api/account", {
            "email": "me@example.com", "name": "新名字", "password": "",
            "host": "127.0.0.1", "port": self.smtp.port, "security": "none",
        })
        self.assertEqual(status, 200)
        self.assertEqual(self.store.stored_password(), "secret-code")
        self.assertEqual(self.smtp.logins, 2)  # every save is verified by a real login

    def test_full_flow_preview_send_and_retry(self):
        self.login()
        _, logo = self.call("/api/upload", LOGO, raw_name="logo.png")
        status, sig = self.call("/api/signature", {"text": "赵采购\n采购部", "logo": {"id": logo["id"], "name": "logo.png"}})
        self.assertEqual(status, 200)
        self.assertIn("data:image/png;base64", sig["html"])
        _, att = self.call("/api/upload", "报价单内容".encode(), raw_name="报价单.xlsx")

        c = self.compose(attachments=[att])
        status, pv = self.call("/api/preview", c)
        self.assertEqual(status, 200, pv)
        self.assertEqual([m["to"] for m in pv["mails"]], ["a@example.com", "b@example.com, b2@example.com"])
        self.assertIn("张经理，您好", pv["mails"][0]["html"])
        self.assertIn("data:image/png;base64", pv["mails"][0]["html"])  # logo shown in preview
        self.assertEqual(pv["mails"][1]["subject"], "【询价】乙公司")

        # boss (cc) refused everywhere: mail to a still goes out, with a warning;
        # mail to b has every address refused, so it fails outright.
        self.smtp.reject_rcpt = {"boss@example.com", "b@example.com", "b2@example.com"}
        self.app_interval(0)
        _, res = self.call("/api/send", {"compose": c})
        st = self.wait(res["job"])
        self.assertEqual([r["status"] for r in st["results"]], ["sent", "failed"])
        self.assertIn("boss@example.com", st["results"][0]["error"])

        rcpts, data = self.smtp.mails[0]
        self.assertEqual(rcpts, ["a@example.com"])
        msg = email.message_from_bytes(data, policy=policy.default)
        self.assertEqual([a.get_filename() for a in msg.iter_attachments()], ["报价单.xlsx"])
        self.assertIn("采购部", msg.get_body(("plain",)).get_content())

        self.smtp.reject_rcpt = None
        _, res = self.call("/api/send/retry", {"job": res["job"]})
        st = self.wait(res["job"])
        self.assertEqual([(r["to"], r["status"]) for r in st["results"]], [("b@example.com, b2@example.com", "sent")])

        # one batch in the send record, the retry updated the same entry
        _, hist = self.call("/api/history")
        (summary,) = hist["batches"]
        self.assertEqual((summary["total"], summary["sent"], summary["failed"]), (2, 2, 0))
        self.assertEqual(summary["attachments"], ["报价单.xlsx"])
        self.assertIn("乙公司", summary["search"])
        _, batch = self.call(f"/api/history/batch?id={summary['id']}")
        self.assertEqual(batch["subject"], "【询价】{{公司}}")
        self.assertIn("boss@example.com", batch["mails"][0]["error"])  # warning kept
        _, mail = self.call(f"/api/history/mail?id={summary['id']}&n=1")
        self.assertEqual(mail["to"], "a@example.com")
        self.assertIn("张经理，您好", mail["html"])
        self.assertIn("data:image/png;base64", mail["html"])  # logo still inline
        self.assertEqual(mail["attachments"], "报价单.xlsx")
        # stored .eml has no attachment payload; the attachment is stored once per batch
        eml = self.download(f"/api/history/file?id={summary['id']}&kind=eml&n=1")
        stored = email.message_from_bytes(eml, policy=policy.default)
        self.assertEqual(list(stored.iter_attachments()), [])
        self.assertEqual(self.download(f"/api/history/file?id={summary['id']}&kind=att&name=报价单.xlsx"), "报价单内容".encode())
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.download(f"/api/history/file?id={summary['id']}&kind=eml&n=1", token="wrong")
        self.assertEqual(cm.exception.code, 403)
        cm.exception.close()

    def test_test_send_goes_only_to_self_once(self):
        self.login()
        self.app_interval(0)
        _, res = self.call("/api/send", {"compose": self.compose(), "test": True})
        st = self.wait(res["job"])
        self.assertEqual(len(st["results"]), 1)
        rcpts, data = self.smtp.mails[0]
        self.assertEqual(rcpts, ["me@example.com"])
        self.assertEqual(self.call("/api/history")[1]["batches"], [])  # test mails are not recorded

    def test_validation_lists_every_problem(self):
        self.login()
        c = self.compose(subject="{{不存在}}", rows=[{"邮箱": "bad-email"}, {"邮箱": "ok@example.com"}])
        status, res = self.call("/api/preview", c)
        self.assertEqual(status, 400)
        self.assertEqual(res["problems"], ["第 1 位收件人: 邮箱格式不正确 bad-email"])

    def test_import_excel_and_templates(self):
        from openpyxl import Workbook
        import io

        wb = Workbook()
        wb.active.append(["E-mail", "公司"])
        wb.active.append(["x@example.com", "丙公司"])
        buf = io.BytesIO()
        wb.save(buf)
        status, table = self.call("/api/import", buf.getvalue(), raw_name="名单.xlsx")
        self.assertEqual(status, 200, table)
        self.assertEqual(table["columns"], ["E-mail", "公司"])
        self.assertEqual(table["rows"], [{"E-mail": "x@example.com", "公司": "丙公司"}])

        _, tpls = self.call("/api/templates", {"name": "询价", "compose": self.compose()})
        self.assertIn("询价", tpls)
        _, state = self.call("/api/state")
        self.assertIn("询价", state["templates"])
        _, tpls = self.call("/api/templates/delete", {"name": "询价"})
        self.assertEqual(tpls, {})

    def test_common_variables(self):
        self.login()
        c = self.compose(
            subject="【询价】{{项目名称}} - {{公司}}",
            body="{{联系人}}，您好：\n\n{{项目代号}} 项目的 {{采购物资}} 询价。",
            variables=[
                {"name": "项目名称", "value": "海上平台"},
                {"name": "项目代号", "value": "P-2026"},
                {"name": "采购物资", "value": "球阀"},
                {"name": "", "value": ""},  # blank row in the UI is ignored
            ],
        )
        status, pv = self.call("/api/preview", c)
        self.assertEqual(status, 200, pv)
        self.assertEqual(pv["mails"][0]["subject"], "【询价】海上平台 - 甲公司")
        self.assertIn("P-2026 项目的 球阀 询价", pv["mails"][0]["html"])

        clash = self.compose(variables=[{"name": "公司", "value": "x"}])
        status, res = self.call("/api/preview", clash)
        self.assertEqual(status, 400)
        self.assertIn("既是收件人表格的列", res["error"])

        missing = self.compose(body="{{项目名称}}", variables=[])
        status, res = self.call("/api/preview", missing)
        self.assertEqual(status, 400)
        self.assertTrue(all("项目名称" in p for p in res["problems"]))

    def test_recipient_lists(self):
        rows = [{"邮箱": "a@example.com", "公司": "甲"}, {"邮箱": "b@example.com", "公司": "乙"}]
        status, lists = self.call("/api/lists", {"name": "阀门供应商", "list": {"columns": ["邮箱", "公司"], "rows": rows}})
        self.assertEqual(status, 200, lists)
        self.assertEqual(lists["阀门供应商"]["rows"], rows)
        _, state = self.call("/api/state")
        self.assertIn("阀门供应商", state["lists"])
        self.assertEqual(self.call("/api/lists", {"name": "空", "list": {"columns": ["邮箱"], "rows": []}})[0], 400)
        _, lists = self.call("/api/lists/delete", {"name": "阀门供应商"})
        self.assertEqual(lists, {})

    def test_email_groups(self):
        status, groups = self.call("/api/groups", {"name": "XX项目抄送组", "emails": "boss@wison.com; tech@wison.com，fin@wison.com"})
        self.assertEqual(status, 200, groups)
        self.assertEqual(groups["XX项目抄送组"], ["boss@wison.com", "tech@wison.com", "fin@wison.com"])
        _, state = self.call("/api/state")
        self.assertIn("XX项目抄送组", state["groups"])
        status, res = self.call("/api/groups", {"name": "坏", "emails": "boss@wison"})
        self.assertEqual(status, 400)
        self.assertIn("boss@wison", res["error"])
        self.assertEqual(self.call("/api/groups", {"name": "空", "emails": ""})[0], 400)
        _, groups = self.call("/api/groups/delete", {"name": "XX项目抄送组"})
        self.assertEqual(groups, {})

    def test_rename_library_items(self):
        self.call("/api/templates", {"name": "询价A", "compose": self.compose()})
        self.call("/api/templates", {"name": "询价B", "compose": self.compose()})
        status, res = self.call("/api/rename", {"kind": "templates", "old": "询价A", "new": "阀门询价"})
        self.assertEqual(status, 200, res)
        self.assertEqual(sorted(res["templates"]), ["询价B", "阀门询价"])
        status, res = self.call("/api/rename", {"kind": "templates", "old": "阀门询价", "new": "询价B"})
        self.assertEqual(status, 400)
        self.assertIn("已经有名为", res["error"])
        self.assertEqual(self.call("/api/rename", {"kind": "groups", "old": "不存在", "new": "x"})[0], 400)
        self.assertEqual(self.call("/api/rename", {"kind": "bogus", "old": "a", "new": "b"})[0], 400)

        self.call("/api/groups", {"name": "抄送组", "emails": "a@x.com"})
        _, res = self.call("/api/rename", {"kind": "groups", "old": "抄送组", "new": "项目抄送组"})
        self.assertEqual(res["groups"], {"项目抄送组": ["a@x.com"]})

    def test_list_save_validates_emails_and_drops_blank_rows(self):
        bad = {"columns": ["邮箱", "公司"], "rows": [{"邮箱": "ok@x.com"}, {"邮箱": "broken"}, {}]}
        status, res = self.call("/api/lists", {"name": "坏名单", "list": bad})
        self.assertEqual(status, 400)
        self.assertEqual(res["problems"], ["第 2 位收件人: 邮箱格式不正确 broken"])
        good = {"columns": ["邮箱", "公司"], "rows": [{"邮箱": "ok@x.com", "公司": "甲"}, {"邮箱": "", "公司": ""}]}
        _, lists = self.call("/api/lists", {"name": "好名单", "list": good})
        self.assertEqual(lists["好名单"]["rows"], [{"邮箱": "ok@x.com", "公司": "甲"}])

    def test_backup_export_and_restore(self):
        self.login()
        self.call("/api/templates", {"name": "球阀询价", "compose": self.compose()})
        self.call("/api/groups", {"name": "抄送组", "emails": "boss@x.com"})
        status, made = self.call("/api/backups/export", {"items": ["templates", "groups", "account"]})
        self.assertEqual(status, 200, made)
        self.assertTrue((self.tmp / "backups" / made["name"]).is_file())
        _, listing = self.call("/api/backups")
        self.assertEqual([f["name"] for f in listing["files"]], [made["name"]])
        self.assertEqual(listing["files"][0]["kind"], "manual")

        # lose the data, then restore it
        self.call("/api/templates/delete", {"name": "球阀询价"})
        self.call("/api/groups/delete", {"name": "抄送组"})
        status, res = self.call("/api/backups/restore", {"name": made["name"], "items": ["templates", "groups"], "mode": "merge"})
        self.assertEqual(status, 200, res)
        self.assertIn("新增 1 个", res["summary"]["邮件模板"])
        self.assertTrue(res["snapshot"].startswith("自动备份/恢复前_"))  # undo point
        _, state = self.call("/api/state")
        self.assertIn("球阀询价", state["templates"])
        self.assertIn("抄送组", state["groups"])

        # a backup picked from elsewhere
        data = (self.tmp / "backups" / made["name"]).read_bytes()
        status, up = self.call("/api/backups/upload", data, raw_name="同事给的.zip")
        self.assertEqual(status, 200, up)
        self.assertEqual(up["name"], "导入的备份/同事给的.zip")
        self.assertEqual(self.call("/api/backups/upload", b"junk", raw_name="x.zip")[0], 400)
        self.assertEqual(self.call("/api/backups/restore", {"name": "../account.json", "items": ["account"]})[0], 400)

        self.app.jobs["x"] = {"done": False}
        status, res = self.call("/api/backups/restore", {"name": made["name"], "items": ["templates"]})
        self.assertEqual(status, 400)
        self.assertIn("正在发送", res["error"])

    def test_hello_and_shutdown_refused_while_sending(self):
        with urllib.request.urlopen(self.base + "/hello") as res:
            self.assertEqual(json.loads(res.read()), {"app": "mailer"})
        self.app.jobs["x"] = {"done": False}
        status, res = self.call("/api/shutdown", {})
        self.assertEqual(status, 400)
        self.assertIn("正在发送", res["error"])

class SentFilingTests(ApiTestCase):
    """Copies of sent mail filed in the Sent folder over IMAP."""

    def setUp(self):
        super().setUp()
        self.imap = FakeImapServer(xlist=True, folders=[
            (r"\HasNoChildren \Inbox", '"INBOX"'),
            (r"\HasNoChildren \Sent", '"&XfJT0ZAB-"'),  # 已发送, modified UTF-7
        ])
        self.addCleanup(self.imap.close)

    def login_with_imap(self, **over):
        req = {
            "email": "me@example.com", "name": "赵采购", "password": "secret-code",
            "host": "127.0.0.1", "port": self.smtp.port, "security": "none",
            "save_sent": True, "imap_host": "127.0.0.1", "imap_port": self.imap.port, "imap_security": "none",
        }
        req.update(over)
        return self.call("/api/account", req)

    def test_account_save_checks_imap_and_finds_sent_folder(self):
        status, acc = self.login_with_imap()
        self.assertEqual(status, 200, acc)
        self.assertEqual(acc["sent_folder"], "已发送")
        self.assertTrue(acc["save_sent"])

        self.imap.password = "other"
        status, res = self.login_with_imap()
        self.assertEqual(status, 400)
        self.assertIn("IMAP", res["error"])

    def test_sent_mail_is_filed_with_bcc_and_attachments(self):
        self.login_with_imap()
        self.app_interval(0)
        _, att = self.call("/api/upload", b"quote", raw_name="q.xlsx")
        _, res = self.call("/api/send", {"compose": self.compose(bcc="audit@example.com", attachments=[att])})
        st = self.wait(res["job"])
        self.assertEqual([(r["status"], r["archived"]) for r in st["results"]], [("sent", "saved"), ("sent", "saved")])
        self.assertEqual(len(self.imap.appended), 2)
        mailbox, flags, data = self.imap.appended[0]
        self.assertEqual((mailbox, flags), ('"&XfJT0ZAB-"', r"(\Seen)"))
        filed = email.message_from_bytes(data, policy=policy.default)
        self.assertEqual(filed["To"], "a@example.com")
        self.assertEqual(filed["Bcc"], "audit@example.com")  # the Sent copy shows who was bcc'd
        self.assertEqual([a.get_filename() for a in filed.iter_attachments()], ["q.xlsx"])
        self.assertTrue(all(b"audit@example.com" not in d for _, d in self.smtp.mails))  # but recipients never see it
        _, hist = self.call("/api/history")
        self.assertEqual(hist["batches"][0]["archived"], 2)

    def test_filing_failure_is_only_a_warning(self):
        self.login_with_imap()
        self.imap.close()  # IMAP server goes away after the account was saved
        self.app_interval(0)
        _, res = self.call("/api/send", {"compose": self.compose()})
        st = self.wait(res["job"])
        self.assertEqual([(r["status"], r["archived"]) for r in st["results"]], [("sent", "failed"), ("sent", "failed")])
        self.assertIn("未能存入“已发送”", st["results"][0]["error"])
        self.assertEqual(len(self.smtp.mails), 2)

    def test_test_send_is_not_filed(self):
        self.login_with_imap()
        self.app_interval(0)
        _, res = self.call("/api/send", {"compose": self.compose(), "test": True})
        self.wait(res["job"])
        self.assertEqual(self.imap.appended, [])

    def test_old_account_gets_imap_settings_from_presets(self):
        self.store._write("account.json", {"email": "me@wison.com", "name": "", "host": "smtp.263.net",
                                           "port": 465, "security": "ssl", "password": ""})
        acc = self.store.account()
        self.assertTrue(acc["save_sent"])
        self.assertEqual((acc["imap_host"], acc["imap_port"], acc["imap_security"]), ("imap.263.net", 143, "starttls"))


class SecretTests(unittest.TestCase):
    def test_round_trip(self):
        stored = secret.encrypt("授权码abc")
        self.assertNotIn("abc", stored)
        self.assertEqual(secret.decrypt(stored), "授权码abc")


if __name__ == "__main__":
    unittest.main()
