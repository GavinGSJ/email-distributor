"""Pure MIME assembly: already-rendered content in, EmailMessage out."""

from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def build_message(
    *,
    from_addr,
    from_name="",
    to,
    cc=(),
    bcc=(),
    reply_to=None,
    subject,
    text,
    html,
    inline_images=(),
    attachments=(),
):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = Address(display_name=from_name, addr_spec=from_addr) if from_name else from_addr
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)  # smtplib.send_message strips this header
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=from_addr.rpartition("@")[2] or None)

    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    if inline_images:
        html_part = msg.get_payload()[1]
        for img in inline_images:
            html_part.add_related(
                img.data, img.maintype, img.subtype, cid=f"<{img.cid}>", disposition="inline"
            )
    for att in attachments:
        msg.add_attachment(att.data, maintype=att.maintype, subtype=att.subtype, filename=att.filename)
    return msg
