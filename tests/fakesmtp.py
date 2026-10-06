import re
import socket
import threading

CRLF = b"\r" + b"\n"


class FakeSmtpServer:
    """Minimal SMTP server: accepts any login, records envelope + raw data of every mail.
    Set `reject_rcpt` to a set of addresses whose RCPT should fail."""

    def __init__(self, reject_rcpt=None):
        self.mails = []  # (rcpts, data)
        self.logins = 0
        self.reject_rcpt = reject_rcpt
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def close(self):
        self.sock.close()

    def _serve(self, conn):
        f = conn.makefile("rwb")

        def say(line):
            f.write(line.encode() + CRLF)
            f.flush()

        say("220 fake")
        rcpts = []
        while True:
            raw = f.readline()
            if not raw:
                break
            line = raw.decode().strip()
            cmd = line.upper()
            if cmd.startswith(("EHLO", "HELO")):
                f.write(b"250-fake" + CRLF)
                say("250 AUTH PLAIN LOGIN")
            elif cmd.startswith("AUTH"):
                self.logins += 1
                say("235 ok")
            elif cmd.startswith("MAIL"):
                rcpts = []
                say("250 ok")
            elif cmd.startswith("RCPT"):
                addr = re.search(r"<(.*)>", line).group(1)
                if addr in (self.reject_rcpt or ()):
                    say("550 no such user")
                else:
                    rcpts.append(addr)
                    say("250 ok")
            elif cmd == "RSET":
                say("250 ok")
            elif cmd == "DATA":
                say("354 go")
                data = b""
                while not data.endswith(CRLF + b"." + CRLF):
                    data += f.readline()
                self.mails.append((rcpts, data))
                say("250 queued")
            elif cmd == "QUIT":
                say("221 bye")
                break
            else:
                say("250 ok")
        conn.close()
