"""Local web UI. Run:  python -m mailer.webapp   (or double-click 邮件助手.pyw)

The server listens on 127.0.0.1 only. Every API call must carry the random token
embedded in the page and a localhost Host header, so other websites open in the
same browser cannot drive it."""

import base64
import email
import email.policy
import json
import mimetypes
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from . import backup, providers
from .archive import SendArchive
from .attachments import load_attachments
from .body import Body
from .errors import ConfigError, MailerError, PrepareError
from .recipients import parse_emails, read_table, recipients_from_table
from .sender import SmtpSender
from .sentbox import SentFolder
from .service import build_mails, send_all
from .store import DEFAULT_ROOT, Store, imap_config_for, smtp_config_for

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"/app.js": "text/javascript; charset=utf-8", "/app.css": "text/css; charset=utf-8"}
MAX_BODY = 60 * 1024 * 1024
SEND_INTERVAL = 2.0
IDLE_EXIT_SECONDS = 300  # quit when no page has talked to us for this long
_BAD_VAR = re.compile(r"[{}\s]")


class App:
    def __init__(self, store, backup_dir=None):
        self.store = store
        self.backups = backup.BackupFolder(backup_dir or Path(store.root).parent / "backups", store.root)
        self.token = secrets.token_urlsafe(24)
        self.jobs = {}
        self.lock = threading.Lock()
        self.server = None
        self.last_seen = time.monotonic()
        self.archive = SendArchive(store.history_dir)

    # -- compose -> messages ----------------------------------------------------
    def build(self, compose, *, test_to=None, limit=None):
        acc = self.store.account()
        if not acc:
            raise ConfigError("请先在“账号设置”里登录发件邮箱")
        subject = (compose.get("subject") or "").strip()
        body = compose.get("body") or ""
        if not subject:
            raise ConfigError("请填写邮件主题")
        if not body.strip():
            raise ConfigError("请填写邮件正文")

        columns = compose.get("columns") or []
        rows = compose.get("rows") or []
        table = [columns] + [[r.get(c, "") for c in columns] for r in rows]
        recipients = recipients_from_table(table, label=lambda n: f"第 {n - 1} 位收件人")
        shared = self._common_variables(compose.get("variables") or [], columns)
        if limit:
            recipients = recipients[:limit]

        attachments = load_attachments([self.store.upload_path(a["id"]) for a in compose.get("attachments") or []])
        return build_mails(
            recipients,
            subject=subject,
            body=Body(body, is_html=False),
            signature=self.store.signature() if compose.get("use_signature", True) else None,
            cc=parse_emails(compose.get("cc", ""), "抄送"),
            bcc=parse_emails(compose.get("bcc", ""), "密送"),
            attachments=attachments,
            variables=shared,
            from_addr=acc["email"],
            from_name=acc.get("name", ""),
            test_to=test_to,
        )

    @staticmethod
    def _common_variables(items, columns):
        shared = {}
        for item in items:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            if _BAD_VAR.search(name):
                raise ConfigError(f"通用变量名“{name}”不能包含空格或花括号")
            if name in shared:
                raise ConfigError(f"通用变量“{name}”重复了")
            if name in columns:
                raise ConfigError(f"“{name}”既是收件人表格的列，又是通用变量，请改个名字")
            shared[name] = item.get("value") or ""
        return shared

    # -- API handlers (each returns a JSON-able value) ---------------------------
    def state(self, _):
        sig = self.store.signature_settings()
        if sig.get("logo"):
            path = self.store.upload_path(sig["logo"]["id"])
            if path.is_file():
                ctype = mimetypes.guess_type(path.name)[0] or "image/png"
                sig["logo"]["preview"] = f"data:{ctype};base64,{base64.b64encode(path.read_bytes()).decode()}"
        return {
            "account": self.store.account(),
            "signature": sig,
            "draft": self.store.draft(),
            "templates": self.store.templates(),
            "lists": self.store.recipient_lists(),
            "groups": self.store.email_groups(),
            "providers": providers.as_json(),
        }

    def save_account(self, req):
        email = parse_emails(req.get("email", ""), "邮箱")
        if len(email) != 1:
            raise ConfigError("请填写一个发件邮箱")
        acc = {
            "email": email[0],
            "name": (req.get("name") or "").strip(),
            "host": (req.get("host") or "").strip(),
            "port": int(req.get("port") or 465),
            "security": req.get("security") or "ssl",
        }
        if not acc["host"]:
            raise ConfigError("请填写 SMTP 服务器地址")
        acc["save_sent"] = bool(req.get("save_sent"))
        acc["imap_host"] = (req.get("imap_host") or "").strip()
        acc["imap_port"] = int(req.get("imap_port") or 993)
        acc["imap_security"] = req.get("imap_security") or "ssl"
        if acc["save_sent"] and not acc["imap_host"]:
            raise ConfigError("开启“存入已发送”需要填写 IMAP 服务器地址")
        password = req.get("password") or ""
        if not password:
            old = self.store.account()
            if not old or old["email"] != acc["email"]:
                raise ConfigError("请填写密码/授权码")
            password = self.store.stored_password()
        sent_folder = ""
        if not req.get("skip_test"):
            with SmtpSender(smtp_config_for(acc, password)):
                pass  # login succeeded
            if acc["save_sent"]:
                try:
                    with SentFolder(imap_config_for(acc, password)) as box:
                        sent_folder = box.connect()
                except MailerError as e:
                    raise ConfigError(f"发信设置正确，但“存入已发送”用的 IMAP 不可用：{e}\n"
                                      "可以检查 IMAP 设置，或先取消勾选“发送后存入已发送”。")
        self.store.save_account(password=password, **acc)
        return {**self.store.account(), "sent_folder": sent_folder}

    def save_signature(self, req):
        self.store.save_signature_settings(req.get("text", ""), req.get("logo"))
        sig = self.store.signature()
        return {"html": _inline_data_uris(sig.html, sig.images) if sig else ""}

    def save_draft(self, req):
        self.store.save_draft(req)
        return {}

    def save_template(self, req):
        self.store.save_template(req.get("name", ""), req.get("compose") or {})
        return self.store.templates()

    def delete_template(self, req):
        self.store.delete_template(req.get("name", ""))
        return self.store.templates()

    def save_list(self, req):
        lst = req.get("list") or {}
        columns = lst.get("columns") or []
        rows = [r for r in lst.get("rows") or [] if any(str(r.get(c, "")).strip() for c in columns)]
        if rows:  # same checks as when sending, so a saved list is always usable
            recipients_from_table([columns] + [[r.get(c, "") for c in columns] for r in rows],
                                  label=lambda n: f"第 {n - 1} 位收件人")
        self.store.save_recipient_list(req.get("name", ""), columns, rows)
        return self.store.recipient_lists()

    def rename(self, req):
        kind = req.get("kind", "")
        return {kind: self.store.rename(kind, req.get("old", ""), req.get("new", ""))}

    def delete_list(self, req):
        self.store.delete_recipient_list(req.get("name", ""))
        return self.store.recipient_lists()

    def save_group(self, req):
        self.store.save_email_group(req.get("name", ""), parse_emails(req.get("emails", ""), "组合中的邮箱"))
        return self.store.email_groups()

    def delete_group(self, req):
        self.store.delete_email_group(req.get("name", ""))
        return self.store.email_groups()

    def ping(self, _):
        return {}

    def shutdown(self, _):
        if self._sending():
            raise ConfigError("还有邮件正在发送，请等发送完成后再退出")
        if self.server:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        return {}

    def _sending(self):
        with self.lock:
            return any(not j["done"] for j in self.jobs.values())

    def watchdog(self):
        while True:
            time.sleep(15)
            if not self._sending() and time.monotonic() - self.last_seen > IDLE_EXIT_SECONDS:
                print("长时间没有打开的页面，自动退出", flush=True)
                self.server.shutdown()
                return

    def preview(self, req):
        mails = self.build(req)
        out = []
        for m in mails:
            msg = m.message
            images = [
                (p["Content-ID"].strip("<>"), p.get_content_type(), p.get_content())
                for p in msg.walk()
                if p["Content-ID"]
            ]
            html = msg.get_body(("html",)).get_content()
            for cid, ctype, data in images:
                html = html.replace(f"cid:{cid}", f"data:{ctype};base64,{base64.b64encode(data).decode()}")
            out.append({"to": m.to, "cc": msg["Cc"] or "", "subject": m.subject, "html": html})
        return {"mails": out}

    def send(self, req):
        test_to = None
        if req.get("test"):
            test_to = self.store.account()["email"] if self.store.account() else None
        compose = req["compose"]
        mails = self.build(compose, test_to=test_to, limit=1 if test_to else None)
        batch = None
        if not test_to:  # test mails are not part of the record
            acc = self.store.account()
            batch = self.archive.create(
                sender=f"{acc.get('name', '')} <{acc['email']}>".strip(),
                subject=(compose.get("subject") or "").strip(),
                cc=compose.get("cc", ""),
                bcc=compose.get("bcc", ""),
                variables=self._common_variables(compose.get("variables") or [], compose.get("columns") or []),
                attachment_paths=[self.store.upload_path(a["id"]) for a in compose.get("attachments") or []],
                mails=mails,
            )
        return {"job": self._start(mails, batch=batch, numbers=list(range(1, len(mails) + 1)))}

    def retry(self, req):
        with self.lock:
            job = self.jobs.get(req.get("job"))
            if not job or not job["done"]:
                raise ConfigError("找不到可重试的发送任务")
            failed = [i for i, r in enumerate(job["results"]) if r["status"] == "failed"]
            mails = [job["mails"][i] for i in failed]
            numbers = [job["numbers"][i] for i in failed]
        if not mails:
            raise ConfigError("没有失败的邮件")
        return {"job": self._start(mails, batch=job["batch"], numbers=numbers)}

    def status(self, query):
        with self.lock:
            job = self.jobs.get(query.get("job", [""])[0])
            if not job:
                raise ConfigError("找不到发送任务")
            return {k: job[k] for k in ("total", "results", "done", "error", "batch")}

    def _start(self, mails, batch, numbers):
        """Send in the background. `batch` is the archive record (None for a test
        send, which is neither recorded nor filed in Sent); `numbers` maps each mail
        to its entry in that record."""
        job_id = uuid.uuid4().hex
        job = {"total": len(mails), "results": [], "done": False, "error": "",
               "mails": mails, "batch": batch, "numbers": numbers}
        with self.lock:
            self.jobs[job_id] = job
        cfg = self.store.smtp_config()
        imap = self.store.imap_config() if batch else None

        def progress(i, _total, result):
            if batch:
                self.archive.update(batch, numbers[i - 1], result)
            with self.lock:
                job["results"].append(asdict(result))

        def run():
            try:
                with SmtpSender(cfg) as sender, (SentFolder(imap) if imap else _Nothing()) as box:
                    send_all(
                        mails,
                        sender,
                        interval=SEND_INTERVAL,
                        skip_sent=False,
                        on_progress=progress,
                        sent_box=box,
                    )
            except MailerError as e:
                with self.lock:
                    job["error"] = str(e)
            except Exception as e:  # never leave the UI spinning
                traceback.print_exc()
                with self.lock:
                    job["error"] = f"内部错误: {e}"
            finally:
                with self.lock:
                    job["done"] = True

        threading.Thread(target=run, daemon=True).start()
        return job_id

    # -- backup & restore ---------------------------------------------------------
    def backup_list(self, _):
        return {
            "folder": str(self.backups.folder),
            "files": self.backups.listing(),
            "items": {k: label for k, (label, _, _) in backup.ITEMS.items()},
        }

    def backup_export(self, req):
        return self.backups.export(req.get("items") or None)

    def backup_upload(self, data, name):
        saved = self.backups.save_uploaded(name, data)
        return {"name": saved, "manifest": backup.inspect(self.backups.resolve(saved))}

    def backup_inspect(self, req):
        return backup.inspect(self.backups.resolve(req.get("name", "")))

    def backup_restore(self, req):
        if self._sending():
            raise ConfigError("还有邮件正在发送，请等发送完成后再恢复")
        path = self.backups.resolve(req.get("name", ""))
        items = [k for k in req.get("items") or [] if k in backup.ITEMS]
        if not items:
            raise ConfigError("请至少选择一项要恢复的内容")
        snapshot = self.backups.snapshot("恢复前")  # so a wrong restore can be undone
        summary = backup.restore(self.store.root, path, items, req.get("mode", "merge"))
        return {
            "summary": {backup.ITEMS[k][0]: v for k, v in summary.items()},
            "snapshot": snapshot.relative_to(self.backups.folder).as_posix(),
        }

    def backup_reveal(self, req):
        target = self.backups.resolve(req["name"]) if req.get("name") else self.backups.folder
        self.backups.folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            if target.is_file():
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(target)
        return {}

    def history(self, _):
        return {"batches": self.archive.summaries()}

    def history_batch(self, query):
        return self.archive.get(query.get("id", [""])[0])

    def history_mail(self, query):
        path = self.archive.mail_path(query.get("id", [""])[0], query.get("n", ["0"])[0])
        msg = email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
        return {
            "from": str(msg["From"] or ""), "to": str(msg["To"] or ""), "cc": str(msg["Cc"] or ""),
            "bcc": str(msg["Bcc"] or ""), "subject": str(msg["Subject"] or ""), "date": str(msg["Date"] or ""),
            "attachments": str(msg["X-Mailer-Attachments"] or ""), "html": _message_html(msg),
        }

    def history_file(self, query):
        """(bytes, filename) for a download link: the .eml of one mail or an attachment."""
        batch_id = query.get("id", [""])[0]
        if query.get("kind", [""])[0] == "eml":
            path = self.archive.mail_path(batch_id, query.get("n", ["0"])[0])
            return path.read_bytes(), path.name
        path = self.archive.attachment_path(batch_id, query.get("name", [""])[0])
        return path.read_bytes(), path.name

    def upload(self, data, name):
        return self.store.save_upload(name, data)

    def import_table(self, data, name):
        info = self.store.save_upload(name, data)
        rows = read_table(self.store.upload_path(info["id"]))
        rows = [r for r in rows if any(str(c).strip() for c in r)]
        if not rows:
            raise ConfigError("文件里没有内容")
        headers = [str(h).strip() or f"列{i + 1}" for i, h in enumerate(rows[0])]
        return {
            "columns": headers,
            "rows": [{h: (r[i] if i < len(r) else "") for i, h in enumerate(headers)} for r in rows[1:]],
        }


class _Nothing:
    """Stand-in context manager when Sent filing is off."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        pass


def _message_html(msg):
    """HTML body of a stored message with inline images turned into data: URIs."""
    part = msg.get_body(("html", "plain"))
    if part is None:
        return ""
    html = part.get_content()
    if part.get_content_type() == "text/plain":
        html = "<pre style='white-space:pre-wrap;font-family:inherit'>" + html.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
    for p in msg.walk():
        if p["Content-ID"]:
            uri = f"data:{p.get_content_type()};base64,{base64.b64encode(p.get_content()).decode()}"
            html = html.replace(f"cid:{p['Content-ID'].strip('<>')}", uri)
    return html


def _inline_data_uris(html, images):
    for img in images:
        uri = f"data:{img.maintype}/{img.subtype};base64,{base64.b64encode(img.data).decode()}"
        html = html.replace(f"cid:{img.cid}", uri)
    return html


def make_handler(app, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    json_posts = {
        "/api/account": app.save_account,
        "/api/signature": app.save_signature,
        "/api/draft": app.save_draft,
        "/api/templates": app.save_template,
        "/api/templates/delete": app.delete_template,
        "/api/preview": app.preview,
        "/api/send": app.send,
        "/api/send/retry": app.retry,
        "/api/lists": app.save_list,
        "/api/lists/delete": app.delete_list,
        "/api/groups": app.save_group,
        "/api/groups/delete": app.delete_group,
        "/api/rename": app.rename,
        "/api/backups/export": app.backup_export,
        "/api/backups/inspect": app.backup_inspect,
        "/api/backups/restore": app.backup_restore,
        "/api/backups/reveal": app.backup_reveal,
        "/api/ping": app.ping,
        "/api/shutdown": app.shutdown,
    }
    raw_posts = {"/api/upload": app.upload, "/api/import": app.import_table, "/api/backups/upload": app.backup_upload}
    gets = {
        "/api/state": app.state,
        "/api/send/status": app.status,
        "/api/backups": app.backup_list,
        "/api/history": app.history,
        "/api/history/batch": app.history_batch,
        "/api/history/mail": app.history_mail,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, status, payload, ctype="application/json; charset=utf-8"):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, api, token=None):
            if self.headers.get("Host") not in allowed_hosts:
                self._reply(403, {"error": "forbidden"})
                return False
            if api and not secrets.compare_digest(token or self.headers.get("X-Token", ""), app.token):
                self._reply(403, {"error": "页面已过期，请刷新"})
                return False
            app.last_seen = time.monotonic()
            return True

        def _run(self, func, *args):
            try:
                self._reply(200, func(*args))
            except PrepareError as e:
                self._reply(400, {"error": "有些邮件无法生成，请检查：", "problems": e.problems})
            except MailerError as e:
                title, *problems = [line.strip() for line in str(e).splitlines() if line.strip()]
                self._reply(400, {"error": title, "problems": problems} if problems else {"error": title})
            except Exception as e:
                traceback.print_exc()
                self._reply(500, {"error": f"内部错误: {e}"})

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                if self._guard(api=False):
                    page = (STATIC / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", app.token)
                    self._reply(200, page.encode(), "text/html; charset=utf-8")
            elif url.path == "/hello":  # lets a second launch find this instance
                if self._guard(api=False):
                    self._reply(200, {"app": "mailer"})
            elif url.path == "/api/history/file":  # plain link (no custom header), so token in the query
                query = parse_qs(url.query)
                if self._guard(api=True, token=query.get("token", [""])[0]):
                    try:
                        data, name = app.history_file(query)
                    except MailerError as e:
                        self._reply(404, {"error": str(e)})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
                    self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name)}")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
            elif url.path in STATIC_FILES:
                if self._guard(api=False):
                    self._reply(200, (STATIC / url.path[1:]).read_bytes(), STATIC_FILES[url.path])
            elif url.path in gets:
                if self._guard(api=True):
                    self._run(gets[url.path], parse_qs(url.query))
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            if not self._guard(api=True):
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._reply(413, {"error": "文件太大"})
                return
            data = self.rfile.read(length)
            if url.path in raw_posts:
                name = unquote(parse_qs(url.query).get("name", ["file"])[0])
                self._run(raw_posts[url.path], data, name)
            elif url.path in json_posts:
                try:
                    req = json.loads(data or b"{}")
                except json.JSONDecodeError:
                    self._reply(400, {"error": "bad json"})
                    return
                self._run(json_posts[url.path], req)
            else:
                self._reply(404, {"error": "not found"})

    return Handler


def _free_port(preferred=8765):
    for port in range(preferred, preferred + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise OSError("找不到可用端口")


def _daily_snapshot(app):
    try:
        made = app.backups.daily_snapshot()
        if made:
            print(f"已自动备份: {made}", flush=True)
    except Exception:  # a failed snapshot must never stop the app
        traceback.print_exc()


def _setup_output(log_path):
    if sys.stdout is None or sys.stderr is None:  # started with pythonw: no console
        log = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    else:
        for stream in (sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _running_instance(info_path):
    """URL of an already running instance, or None."""
    try:
        port = json.loads(info_path.read_text(encoding="utf-8"))["port"]
        url = f"http://127.0.0.1:{port}/"
        with urllib.request.urlopen(url + "hello", timeout=1) as res:
            if json.loads(res.read()).get("app") == "mailer":
                return url
    except (OSError, ValueError, KeyError):
        pass
    return None


def main(open_browser=True):
    DEFAULT_ROOT.mkdir(parents=True, exist_ok=True)
    _setup_output(DEFAULT_ROOT / "app.log")
    info_path = DEFAULT_ROOT / "server.json"
    url = _running_instance(info_path)
    if url:
        print(f"邮件助手已在运行，打开页面: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return

    port = _free_port()
    app = App(Store())
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    app.server = server
    info_path.write_text(json.dumps({"port": port}), encoding="utf-8")
    url = f"http://127.0.0.1:{port}/"
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 邮件助手已启动: {url}", flush=True)
    threading.Thread(target=app.watchdog, daemon=True).start()
    threading.Thread(target=_daily_snapshot, args=(app,), daemon=True).start()
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        try:
            if json.loads(info_path.read_text(encoding="utf-8")).get("port") == port:
                info_path.unlink()
        except (OSError, ValueError):
            pass
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 邮件助手已退出", flush=True)


if __name__ == "__main__":
    main(open_browser="--no-browser" not in sys.argv)
