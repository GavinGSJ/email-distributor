"""SMTP transport. Anything with a `send(message)` method can stand in for it."""

import functools
import ipaddress
import smtplib
import socket
import ssl

from .errors import SendError


@functools.cache
def _local_hostname():
    # smtplib calls getfqdn() on every connection, which can take seconds on Windows.
    return socket.getfqdn()


def _network_hint(host):
    """Proxy/VPN tools in TUN "fake-ip" mode resolve every name into 198.18.0.0/15 and
    often block mail ports; say so instead of leaving a bare socket error."""
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(host))
    except (OSError, ValueError):
        return ""
    if ip in ipaddress.ip_network("198.18.0.0/15"):
        return (
            f"\n原因：{host} 被解析成了 {ip}，这是代理/VPN（TUN 模式）或安全软件接管网络时使用的假地址，"
            "这类软件通常会拦截发信端口。请彻底退出 VPN（含后台服务/TUN 模式）后运行 ipconfig /flushdns 再试，"
            f"或在 VPN 规则里把 {host} 设为直连；如仍不行，请联系 IT 允许 python.exe 发送邮件。"
        )
    return ""


class SmtpSender:
    def __init__(self, config):
        self._cfg = config
        self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()

    def connect(self):
        cfg = self._cfg
        try:
            if cfg.security == "ssl":
                conn = smtplib.SMTP_SSL(
                    cfg.host,
                    cfg.port,
                    local_hostname=_local_hostname(),
                    timeout=cfg.timeout,
                    context=ssl.create_default_context(),
                )
            else:
                conn = smtplib.SMTP(cfg.host, cfg.port, local_hostname=_local_hostname(), timeout=cfg.timeout)
                conn.ehlo()
                if cfg.security == "starttls":
                    conn.starttls(context=ssl.create_default_context())
                    conn.ehlo()
            if cfg.username:
                conn.login(cfg.username, cfg.password)
        except smtplib.SMTPAuthenticationError as e:
            raise SendError(f"SMTP 登录失败（很多邮箱需要用“授权码”而不是登录密码）: {e}")
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"无法连接 SMTP 服务器 {cfg.host}:{cfg.port}: {e}{_network_hint(cfg.host)}")
        self._conn = conn

    def send(self, message):
        """Returns {address: (code, reason)} for recipients the server refused while
        accepting the others (smtplib only raises when *all* are refused)."""
        if self._conn is None:
            self.connect()
        try:
            try:
                return self._conn.send_message(message)
            except smtplib.SMTPServerDisconnected:  # idle connection dropped; retry once
                self.connect()
                return self._conn.send_message(message)
        except smtplib.SMTPRecipientsRefused as e:
            raise SendError(f"收件人被服务器拒绝: {e.recipients}")
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"发送失败: {e}")

    def close(self):
        if self._conn is not None:
            try:
                self._conn.quit()
            except (smtplib.SMTPException, OSError):
                pass  # some servers (e.g. QQ) drop the connection on QUIT
            self._conn = None
