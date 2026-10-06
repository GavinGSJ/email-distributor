import re
import socket
import threading

CRLF = b"\r" + b"\n"


class FakeImapServer:
    """Minimal IMAP4rev1 server: LOGIN, ID, LIST/XLIST, APPEND. Records appended mail.

    folders: [(flags, name)] as sent in LIST responses, e.g. ("\\HasNoChildren \\Sent", '"Sent Items"').
    xlist:   advertise and answer XLIST (263 style) instead of flagging folders in LIST."""

    def __init__(self, folders=None, xlist=False, password="secret-code"):
        self.folders = folders or [("\\HasNoChildren", '"INBOX"'), ("\\HasNoChildren \\Sent", '"Sent Items"')]
        self.xlist = xlist
        self.password = password
        self.appended = []  # (mailbox, flags, data)
        self.logins = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self):
        self.sock.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        f = conn.makefile("rwb")

        def say(line):
            f.write(line.encode() + CRLF)
            f.flush()

        caps = "IMAP4rev1 ID AUTH=PLAIN" + (" XLIST" if self.xlist else "")
        say("* OK fake imap ready")
        while True:
            raw = f.readline()
            if not raw:
                break
            line = raw.decode().rstrip("\r\n")
            tag, _, rest = line.partition(" ")
            cmd, _, args = rest.partition(" ")
            cmd = cmd.upper()
            if cmd == "CAPABILITY":
                say(f"* CAPABILITY {caps}")
                say(f"{tag} OK done")
            elif cmd == "LOGIN":
                m = re.match(r'(\S+) "?(.*?)"?$', args)
                if m and m.group(2) == self.password:
                    self.logins += 1
                    say(f"{tag} OK logged in")
                else:
                    say(f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials")
            elif cmd == "ID":
                say('* ID ("name" "fake")')
                say(f"{tag} OK done")
            elif cmd in ("LIST", "XLIST"):
                for flags, name in self.folders:
                    if cmd == "LIST" and self.xlist:
                        flags = " ".join(x for x in flags.split() if x.lower() != "\\sent")
                    say(f'* {cmd} ({flags}) "/" {name}')
                say(f"{tag} OK done")
            elif cmd == "APPEND":
                m = re.match(r'(".*?"|\S+) (\(.*?\)) ".*?" \{(\d+)\}$', args)
                say("+ go ahead")
                data = f.read(int(m.group(3)))
                f.readline()  # CRLF after the literal
                self.appended.append((m.group(1), m.group(2), data))
                say(f"{tag} OK appended")
            elif cmd == "LOGOUT":
                say("* BYE")
                say(f"{tag} OK bye")
                break
            else:
                say(f"{tag} BAD unknown command")
        conn.close()
