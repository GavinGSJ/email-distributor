"""Known SMTP/IMAP settings, so users only have to type their address and password.
IMAP is used to file a copy of each sent mail in the mailbox's Sent folder."""


def _p(name, host, port, security, imap_host, imap_port, imap_security, help):
    return {
        "name": name, "host": host, "port": port, "security": security,
        "imap_host": imap_host, "imap_port": imap_port, "imap_security": imap_security, "help": help,
    }


_QQ_HELP = "QQ 邮箱网页版 → 设置 → 账号 → 开启 IMAP/SMTP 服务 → 生成授权码"
_OUTLOOK_HELP = "使用 Outlook 登录密码（开启两步验证时用应用密码）"

PERSONAL = {
    "qq.com": _p("QQ 邮箱", "smtp.qq.com", 465, "ssl", "imap.qq.com", 993, "ssl", _QQ_HELP),
    "foxmail.com": _p("Foxmail", "smtp.qq.com", 465, "ssl", "imap.qq.com", 993, "ssl", _QQ_HELP),
    "163.com": _p("网易 163 邮箱", "smtp.163.com", 465, "ssl", "imap.163.com", 993, "ssl", "163 邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启 IMAP/SMTP 并获取授权码"),
    "126.com": _p("网易 126 邮箱", "smtp.126.com", 465, "ssl", "imap.126.com", 993, "ssl", "126 邮箱网页版 → 设置 → POP3/SMTP/IMAP → 开启 IMAP/SMTP 并获取授权码"),
    "yeah.net": _p("网易 yeah.net", "smtp.yeah.net", 465, "ssl", "imap.yeah.net", 993, "ssl", "邮箱设置 → POP3/SMTP/IMAP → 开启 IMAP/SMTP 并获取授权码"),
    "sina.com": _p("新浪邮箱", "smtp.sina.com", 465, "ssl", "imap.sina.com", 993, "ssl", "邮箱设置 → 客户端 POP/IMAP/SMTP → 开启并获取授权码"),
    "aliyun.com": _p("阿里邮箱", "smtp.aliyun.com", 465, "ssl", "imap.aliyun.com", 993, "ssl", "使用邮箱登录密码"),
    "139.com": _p("139 邮箱", "smtp.139.com", 465, "ssl", "imap.139.com", 993, "ssl", "邮箱设置 → POP/SMTP/IMAP → 开启并获取授权码"),
    "gmail.com": _p("Gmail", "smtp.gmail.com", 465, "ssl", "imap.gmail.com", 993, "ssl", "Google 账号 → 安全性 → 两步验证 → 应用专用密码"),
    "outlook.com": _p("Outlook", "smtp-mail.outlook.com", 587, "starttls", "outlook.office365.com", 993, "ssl", _OUTLOOK_HELP),
    "hotmail.com": _p("Hotmail", "smtp-mail.outlook.com", 587, "starttls", "outlook.office365.com", 993, "ssl", _OUTLOOK_HELP),
}

# Company mailboxes use their own domain, so the user picks the hosting provider.
ENTERPRISE = [
    # 263: port 143 + STARTTLS is what works reliably (993 is often blocked on company networks).
    _p("263 企业邮箱", "smtp.263.net", 465, "ssl", "imap.263.net", 143, "starttls",
       "一般为邮箱登录密码；如管理员开启了客户端专用密码，请在 263 网页版设置中获取"),
    _p("腾讯企业邮箱", "smtp.exmail.qq.com", 465, "ssl", "imap.exmail.qq.com", 993, "ssl",
       "企业邮箱网页版 → 设置 → 客户端设置 → 开启 IMAP/SMTP，并在“微信绑定/安全登录”里生成客户端专用密码"),
    _p("阿里企业邮箱", "smtp.qiye.aliyun.com", 465, "ssl", "imap.qiye.aliyun.com", 993, "ssl",
       "一般为邮箱登录密码；管理员开启三方客户端安全密码时需用该密码"),
    _p("网易企业邮箱", "smtp.qiye.163.com", 465, "ssl", "imap.qiye.163.com", 993, "ssl", "企业邮箱网页版 → 设置 → 客户端授权密码"),
    _p("Microsoft 365 / Exchange", "smtp.office365.com", 587, "starttls", "outlook.office365.com", 993, "ssl",
       "公司 Microsoft 账号密码（需管理员允许 SMTP/IMAP 认证）"),
]


def imap_for_smtp(smtp_host):
    """Best-guess IMAP settings for an SMTP host: a known provider, else imap.<domain>."""
    for p in [*PERSONAL.values(), *ENTERPRISE]:
        if p["host"] == smtp_host:
            return p["imap_host"], p["imap_port"], p["imap_security"]
    if smtp_host.startswith("smtp."):
        return "imap." + smtp_host[5:], 993, "ssl"
    return None


def as_json():
    return {"personal": PERSONAL, "enterprise": ENTERPRISE}
