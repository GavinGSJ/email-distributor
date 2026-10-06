import email
import email.policy
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from mailer.config import SmtpConfig, load_job
from mailer.errors import MissingVariableError, PrepareError, RecipientError, SendError
from mailer.recipients import load_recipients
from mailer.sender import SmtpSender
from mailer.sendlog import SendLog
from mailer.service import prepare, send_all
from mailer.template import render

from fakesmtp import FakeSmtpServer

DEMO = Path(__file__).resolve().parent.parent / "jobs" / "rfq_demo" / "job.toml"
CRLF = b"\r" + b"\n"


class FakeSender:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], set(fail_for)

    def send(self, message):
        if message["To"] in self.fail_for:
            raise SendError("boom")
        self.sent.append(message)


class TemplateTests(unittest.TestCase):
    def test_render_and_missing(self):
        self.assertEqual(render("你好 {{ 名字 }}", {"名字": "老王"}), "你好 老王")
        with self.assertRaises(MissingVariableError) as cm:
            render("{{a}} {{b}}", {"a": 1})
        self.assertEqual(cm.exception.names, ["b"])

    def test_html_escape(self):
        self.assertEqual(render("{{x}}", {"x": "<b>"}, escape=True), "&lt;b&gt;")


class RecipientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write(self, name, content, encoding="utf-8"):
        p = self.tmp / name
        p.write_bytes(content.encode(encoding))
        return p

    def test_gbk_csv_and_multi_email(self):
        p = self.write("a.csv", "邮箱,公司\na@x.com; b@x.com,甲\n", encoding="gbk")
        (r,) = load_recipients(p)
        self.assertEqual(r.emails, ("a@x.com", "b@x.com"))
        self.assertEqual(r.fields["公司"], "甲")

    def test_reports_all_problems_with_real_line_numbers(self):
        p = self.write("a.csv", "email\nnot-an-email\n\na@x.com\nA@X.com\n")
        with self.assertRaises(RecipientError) as cm:
            load_recipients(p)
        msg = str(cm.exception)
        self.assertIn("第 2 行", msg)
        self.assertIn("第 5 行", msg)  # duplicate, case-insensitive; blank line 3 still counted

    def test_excel(self):
        from openpyxl import Workbook

        wb = Workbook()
        wb.active.append(["email", "公司", "编号"])
        wb.active.append(["a@x.com", "甲", 7.0])
        p = self.tmp / "a.xlsx"
        wb.save(p)
        (r,) = load_recipients(p)
        self.assertEqual(r.fields["编号"], "7")


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.job = load_job(DEMO)

    def test_one_independent_mail_per_recipient_with_shared_cc(self):
        mails = prepare(self.job, "me@example.com", "我")
        self.assertEqual(len(mails), 3)
        tos = [m.message["To"] for m in mails]
        self.assertEqual(tos[1], "supplier_b@example.com, sales_b@example.com")
        for m in mails:
            self.assertEqual(m.message["Cc"], "manager@example.com, finance@example.com")
            for other in tos:
                if other != m.message["To"]:
                    self.assertNotIn(other, m.message["To"] + m.message["Cc"])

    def test_personalised_content_signature_and_attachment(self):
        msg = prepare(self.job, "me@example.com")[0].message
        plain = msg.get_body(("plain",)).get_content()
        html = msg.get_body(("html",)).get_content()
        self.assertIn("张经理，您好", plain)
        self.assertIn("甲供应商有限公司", plain)
        self.assertIn("赵采购", plain)  # signature in text part
        self.assertIn("赵采购", html)  # and html part
        self.assertIn("cid:", html)  # logo embedded inline
        self.assertEqual([a.get_filename() for a in msg.iter_attachments()], ["询价技术要求.txt"])
        att = next(msg.iter_attachments())
        self.assertIn("询价技术要求说明".encode("utf-8"), att.get_content())  # bytes unchanged
        inline = [p for p in msg.walk() if p.get_content_type() == "image/png"]
        self.assertEqual(len(inline), 1)

    def test_test_mode_redirects_and_drops_cc(self):
        mails = prepare(self.job, "me@example.com", test_to="me@example.com")
        for m in mails:
            self.assertEqual(m.message["To"], "me@example.com")
            self.assertIsNone(m.message["Cc"])
            self.assertTrue(m.subject.startswith("[测试]"))

    def test_missing_variable_blocks_everything(self):
        job = replace(self.job, subject="{{没有这个变量}}")
        with self.assertRaises(PrepareError) as cm:
            prepare(job, "me@example.com")
        self.assertEqual(len(cm.exception.problems), 3)

    def test_failure_isolated_and_resume_skips_sent(self):
        log_path = Path(tempfile.mkdtemp()) / "x.jsonl"
        self.addCleanup(shutil.rmtree, log_path.parent)
        mails = prepare(self.job, "me@example.com")
        sender = FakeSender(fail_for={"supplier_b@example.com, sales_b@example.com"})
        res = send_all(mails, sender, log=SendLog(log_path), interval=2, sleep=lambda s: None)
        self.assertEqual([r.status for r in res], ["sent", "failed", "sent"])
        self.assertEqual(len(sender.sent), 2)

        sender2 = FakeSender()
        res2 = send_all(mails, sender2, log=SendLog(log_path), sleep=lambda s: None)
        self.assertEqual([r.status for r in res2], ["skipped", "sent", "skipped"])
        self.assertEqual(len(sender2.sent), 1)

    def test_real_smtp_envelope_includes_cc_and_bcc_but_header_hides_bcc(self):
        job = replace(self.job, bcc=("audit@example.com",))
        mail = prepare(job, "me@example.com")[0]
        server = FakeSmtpServer()
        cfg = SmtpConfig("127.0.0.1", server.port, "none", "", "", "me@example.com", timeout=5)
        with SmtpSender(cfg) as sender:
            sender.send(mail.message)
        ((rcpts, data),) = server.mails
        self.assertEqual(
            sorted(rcpts),
            ["audit@example.com", "finance@example.com", "manager@example.com", "supplier_a@example.com"],
        )
        self.assertNotIn(b"audit@example.com", data)  # Bcc header must not leak


if __name__ == "__main__":
    unittest.main()


class SentFolderTests(unittest.TestCase):
    def setUp(self):
        from fakeimap import FakeImapServer

        self.make = FakeImapServer

    def box(self, server):
        from mailer.sentbox import ImapConfig, SentFolder

        self.addCleanup(server.close)
        return SentFolder(ImapConfig("127.0.0.1", server.port, "none", "me@example.com", "secret-code", timeout=5))

    def test_decode_modified_utf7(self):
        from mailer.sentbox import decode_mutf7

        self.assertEqual(decode_mutf7("&XfJT0ZAB-"), "已发送")
        self.assertEqual(decode_mutf7("INBOX"), "INBOX")
        self.assertEqual(decode_mutf7("A&-B"), "A&B")

    def test_finds_folder_by_list_flag(self):
        server = self.make(folders=[(r"\HasNoChildren", '"INBOX"'), (r"\HasNoChildren \Sent", '"Sent Items"')])
        with self.box(server) as box:
            self.assertEqual(box.connect(), "Sent Items")
            self.assertEqual(box.folder, '"Sent Items"')

    def test_falls_back_to_folder_name(self):
        server = self.make(folders=[(r"\HasNoChildren", '"INBOX"'), (r"\HasNoChildren", '"&XfJT0ZAB-"')])
        with self.box(server) as box:
            self.assertEqual(box.connect(), "已发送")

    def test_no_sent_folder_fails_fast_afterwards(self):
        server = self.make(folders=[(r"\HasNoChildren", '"INBOX"')])
        box = self.box(server)
        with self.assertRaises(SendError) as cm:
            box.save(email.message_from_string("Subject: x\n\nbody", policy=email.policy.default))
        self.assertIn("找不到“已发送”", str(cm.exception))
        logins = server.logins
        with self.assertRaises(SendError):  # no second connection attempt
            box.save(email.message_from_string("Subject: y\n\nbody", policy=email.policy.default))
        self.assertEqual(server.logins, logins)
        box.close()


class RefusedRecipientTests(unittest.TestCase):
    def test_refused_to_is_failure_but_refused_cc_is_warning(self):
        class Refusing:
            def __init__(self, refused):
                self.refused = refused

            def send(self, message):
                return {a: (550, b"no such user") for a in self.refused}

        mails = prepare(load_job(DEMO), "me@example.com")[:1]  # To a@, Cc manager@ + finance@
        (r,) = send_all(mails, Refusing({"supplier_a@example.com"}), sleep=lambda s: None)
        self.assertEqual(r.status, "failed")
        self.assertIn("抄送/密送已收到", r.error)
        (r,) = send_all(mails, Refusing({"finance@example.com"}), sleep=lambda s: None)
        self.assertEqual(r.status, "sent")
        self.assertIn("finance@example.com", r.error)
