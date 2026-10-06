"""Orchestration. This is the only module that knows how the others fit together;
a future GUI should call prepare() / send_all() and never touch the rest."""

import time
from dataclasses import dataclass
from email.utils import getaddresses

from .attachments import load_attachments
from .body import html_to_text, load_body, text_to_html
from .builder import build_message
from .errors import MailerError, MissingVariableError, PrepareError
from .recipients import load_recipients
from .signature import load_signature
from .template import render


@dataclass(frozen=True)
class PreparedMail:
    recipient: object
    to: str  # display form of the To header, also the send-log key
    subject: str
    message: object


@dataclass(frozen=True)
class SendResult:
    to: str
    subject: str
    status: str  # "sent" | "skipped" | "failed"
    error: str = ""  # failure reason, or a warning on a sent mail
    archived: str = ""  # copy in the mailbox's Sent folder: "saved" | "failed" | "" (not attempted)


def prepare(job, from_addr, from_name="", *, test_to=None, only=None):
    """Build every message without touching the network. If anything is wrong in
    any recipient, raises PrepareError listing all problems, so a bad template is
    caught before the first mail goes out.

    test_to: send everything to this address instead (no cc/bcc, subject tagged).
    only:    restrict to recipients having one of these addresses."""
    recipients = load_recipients(job.recipients, job.sheet)
    if only:
        wanted = {e.lower() for e in only}
        recipients = [r for r in recipients if wanted & {e.lower() for e in r.emails}]
        if not recipients:
            raise PrepareError([f"名单里找不到指定的收件人: {sorted(only)}"])

    return build_mails(
        recipients,
        subject=job.subject,
        body=load_body(job.body),
        signature=load_signature(job.signature) if job.signature else None,
        cc=job.cc,
        bcc=job.bcc,
        reply_to=job.reply_to,
        attachments=load_attachments(job.attachments, job.max_attachment_mb),
        variables=job.variables,
        from_addr=from_addr,
        from_name=from_name,
        test_to=test_to,
    )


def build_mails(
    recipients,
    *,
    subject,
    body,
    signature=None,
    cc=(),
    bcc=(),
    reply_to=None,
    attachments=(),
    variables=None,
    from_addr,
    from_name="",
    test_to=None,
):
    """Same as prepare() but from already-loaded pieces (used by the web UI)."""
    if test_to:
        cc, bcc = (), ()
    images = signature.images if signature else ()
    shared_vars = variables or {}
    template_subject = subject

    mails, problems = [], []
    for r in recipients:
        variables = {**shared_vars, **r.fields}
        try:
            subject = render(template_subject, variables)
            if body.is_html:
                body_html = render(body.text, variables, escape=True)
                body_text = html_to_text(body_html)
            else:
                body_text = render(body.text, variables)
                body_html = text_to_html(body_text)
        except MissingVariableError as e:
            problems.append(f"{r.source} ({', '.join(r.emails)}): {e}")
            continue

        if signature:
            body_text = f"{body_text}\n\n{signature.text}"
            body_html = f"{body_html}{signature.html}"
        html = f'<html><body style="font-family:Microsoft YaHei,Arial,sans-serif;font-size:14px">{body_html}</body></html>'

        to = (test_to,) if test_to else r.emails
        if test_to:
            subject = f"[测试] {subject}"
        message = build_message(
            from_addr=from_addr,
            from_name=from_name,
            to=to,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
            subject=subject,
            text=body_text,
            html=html,
            inline_images=images,
            attachments=attachments,
        )
        mails.append(PreparedMail(r, ", ".join(to), subject, message))

    if problems:
        raise PrepareError(problems)
    return mails


def send_all(
    mails, sender, *, log=None, interval=0.0, skip_sent=True, on_progress=None, sleep=time.sleep, sent_box=None
):
    """Send each mail independently. One failure never stops the rest.
    With `sent_box` (anything with save(message)), each delivered mail is also
    filed in the Sent folder; a filing failure is only a warning.
    Returns a SendResult per mail, in order."""
    results = []
    last_sent = False
    for i, mail in enumerate(mails, start=1):
        if skip_sent and log and log.was_sent(mail.to, mail.subject):
            result = SendResult(mail.to, mail.subject, "skipped", "之前已发送成功")
        else:
            if last_sent and interval:
                sleep(interval)
            try:
                refused = sender.send(mail.message) or {}
                to_addrs = {a.lower() for _, a in getaddresses([str(mail.message["To"])])}
                refused_lower = {a.lower() for a in refused}
                if to_addrs and to_addrs <= refused_lower:
                    # Only cc/bcc got it: the person this mail is for did not.
                    status, warnings = "failed", [f"收件人地址被服务器拒收（抄送/密送已收到）: {', '.join(refused)}"]
                else:
                    status = "sent"
                    warnings = [f"已发出，但这些地址被服务器拒收: {', '.join(refused)}"] if refused else []
                archived = ""
                if sent_box is not None:
                    try:
                        sent_box.save(mail.message)
                        archived = "saved"
                    except MailerError as e:
                        archived = "failed"
                        warnings.append(f"已发出，但未能存入“已发送”: {e}")
                result = SendResult(mail.to, mail.subject, status, "；".join(warnings), archived)
            except MailerError as e:
                result = SendResult(mail.to, mail.subject, "failed", str(e))
            if log:
                log.record(result.to, result.subject, result.status, result.error)
        last_sent = result.status == "sent"
        results.append(result)
        if on_progress:
            on_progress(i, len(mails), result)
    return results
