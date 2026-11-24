"""Exercises the real SMTP code path against a tiny local SMTP server (no real email is sent)."""

import base64
import socketserver
import threading
from email import message_from_bytes, policy

from app import outreach
from app.leadutil import LeadData, upsert_lead


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        server = self.server  # type: ignore[assignment]
        self.reply("220 test ESMTP")
        data_mode, buf = False, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            if data_mode:
                if raw in (b".\r\n", b".\n"):
                    server.messages.append(b"".join(buf))
                    data_mode, buf = False, []
                    self.reply("250 queued")
                else:
                    buf.append(raw[1:] if raw.startswith(b"..") else raw)
                continue
            line = raw.decode().strip()
            cmd = line.split(" ")[0].upper()
            if cmd == "EHLO":
                self.wfile.write(b"250-test\r\n250 AUTH PLAIN\r\n")
            elif cmd == "AUTH":
                user, pw = base64.b64decode(line.split(" ")[-1]).decode().split("\0")[1:]
                server.logins.append((user, pw))
                self.reply("235 ok")
            elif cmd in ("MAIL", "RCPT"):
                if cmd == "RCPT":
                    server.rcpts.append(line)
                self.reply("250 ok")
            elif cmd == "DATA":
                data_mode = True
                self.reply("354 go")
            elif cmd == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("250 ok")


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _Handler)
        self.messages: list[bytes] = []
        self.logins: list[tuple[str, str]] = []
        self.rcpts: list[str] = []


def test_real_smtp_path_sends_individual_utf8_messages(settings, session_factory, db):
    server = _Server()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings.smtp_host, settings.smtp_port, settings.smtp_security = "127.0.0.1", server.server_address[1], "none"
        leads = []
        for i in range(2):
            lead, _ = upsert_lead(db, LeadData(name=f"Åkesson Advokatbyrå {i}", email=f"info@firm{i}.example",
                                               website=f"https://firm{i}.example", city=f"Stad{i}"))
            leads.append(lead)
        db.commit()
        t = next(t for t in outreach.list_templates() if t["key"] == "interview_request" and t["language"] == "sv")
        campaign = outreach.create_campaign(db, settings, "smtp", t["subject"], t["body"], "sv", [l.id for l in leads])
        counts = outreach.run_send(session_factory, campaign.id, settings, sleep=lambda s: None)
    finally:
        server.shutdown()
        server.server_close()

    assert counts == {"sent": 2, "failed": 0, "skipped": 0}
    assert server.logins == [("ali.nasrullah@zarintech.se", "secret")] * 2
    assert len(server.rcpts) == 2 and "firm0.example" in server.rcpts[0] and "firm1.example" in server.rcpts[1]
    msg = message_from_bytes(server.messages[0], policy=policy.default)
    assert msg["To"] == "info@firm0.example" and msg["Cc"] is None
    assert "Åkesson Advokatbyrå 0" in msg["Subject"]
    text = msg.get_body().get_content()
    assert "Åkesson Advokatbyrå 0" in text and "avregistrera" in text and "559999-9999" in text
