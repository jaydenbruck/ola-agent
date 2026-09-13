"""Real local sockets exercise the wire; no mail or calendar accounts are contacted."""
import asyncio
import ipaddress
import json
import socketserver
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ola.tools import calendar, mail


@pytest.fixture
def credentials(monkeypatch):
    for name, value in {"OLA_MAIL_USER": "ola@example.test", "OLA_MAIL_APP_PASSWORD": "test-secret",
                        "OLA_GOOGLE_CLIENT_ID": "test-id", "OLA_GOOGLE_CLIENT_SECRET": "test-secret",
                        "OLA_GOOGLE_REFRESH_TOKEN": "test-refresh", "OLA_TIMEZONE": "Europe/Berlin"}.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def tls(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
            .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    certfile, keyfile = tmp_path / "cert.pem", tmp_path / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(certfile, keyfile)
    client = ssl.create_default_context(cafile=str(certfile))
    return server, client


@pytest.fixture
def mail_server(credentials, tls, monkeypatch):
    server_tls, client_tls = tls
    state = {"commands": [], "messages": [], "tls": False, "refuse": False, "fail": None}

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            def send(value):
                self.wfile.write(value.encode() + b"\r\n")
                self.wfile.flush()
            send("220 local test")
            while line := self.rfile.readline():
                verb, *rest = line.decode().strip().split(" ", 1)
                command = " ".join([verb.upper(), *rest])
                state["commands"].append(command.split(" ")[0])
                if command.startswith("EHLO"):
                    send("250-local\r\n250-STARTTLS\r\n250 AUTH PLAIN")
                elif command == "STARTTLS":
                    send("220 ready")
                    self.connection = server_tls.wrap_socket(self.connection, server_side=True)
                    self.rfile, self.wfile = self.connection.makefile("rb"), self.connection.makefile("wb")
                    state["tls"] = True
                elif command.startswith("AUTH"):
                    assert state["tls"]
                    send("535 rejected" if state["fail"] == "auth" else "235 welcome")
                elif command.startswith("RCPT"):
                    send("550 rejected" if state["refuse"] and "bad@" in command else "250 accepted")
                elif command == "DATA":
                    send("354 data")
                    chunks = []
                    while (line := self.rfile.readline()) != b".\r\n":
                        if not line:
                            return
                        chunks.append(line)
                    state["messages"].append(b"".join(chunks))
                    send("250 queued")
                elif command == "QUIT":
                    send("221 bye")
                    break
                else:
                    send("250 ok")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("OLA_MAIL_HOST", "127.0.0.1")
        monkeypatch.setenv("OLA_MAIL_PORT", str(server.server_address[1]))
        monkeypatch.setattr(mail.ssl, "create_default_context", lambda: client_tls)
        yield state
        server.shutdown()
        thread.join()


@pytest.fixture
def imap_server(credentials, tls, monkeypatch):
    server_tls, client_tls = tls
    state = {"commands": [], "query": None, "empty": False, "fail": None}
    raw = b"From: friend@example.test\r\nSubject: =?utf-8?q?Gr=C3=BC=C3=9Fe?=\r\nMessage-ID: <test>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\nHello\r\n"

    class Handler(socketserver.StreamRequestHandler):
        def setup(self):
            self.request = server_tls.wrap_socket(self.request, server_side=True)
            super().setup()

        def handle(self):
            def send(value):
                self.wfile.write(value.encode() + b"\r\n")
                self.wfile.flush()
            send("* OK test IMAP")
            while line := self.rfile.readline():
                text = line.decode().strip()
                tag, command, *args = text.split(" ")
                state["commands"].append(" ".join([command, *args]) if command != "LOGIN" else "LOGIN")
                if command == "CAPABILITY":
                    send("* CAPABILITY IMAP4rev1")
                elif command == "EXAMINE":
                    if state["fail"] == "select":
                        send(tag + " NO rejected")
                        continue
                    send("* 2 EXISTS")
                elif command == "UID" and args[0] == "SEARCH":
                    if args[-1].startswith("{"):
                        send("+ continue")
                        state["query"] = self.rfile.read(int(args[-1][1:-1]))
                        self.rfile.readline()
                    if state["fail"] == "search":
                        send(tag + " NO rejected")
                        continue
                    send("* SEARCH" + ("" if state["empty"] else " 4 9"))
                elif command == "UID" and args[0] == "FETCH":
                    if state["fail"] == "fetch":
                        send(tag + " NO rejected")
                        continue
                    send(f"* 1 FETCH (UID {args[1]} BODY[] {{{len(raw)}}}")
                    self.wfile.write(raw + b")\r\n")
                elif command == "LOGOUT":
                    send("* BYE done")
                    send(tag + " OK logout")
                    break
                send(tag + " OK done")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("OLA_MAIL_IMAP_HOST", "127.0.0.1")
        monkeypatch.setenv("OLA_MAIL_IMAP_PORT", str(server.server_address[1]))
        monkeypatch.setattr(mail.ssl, "create_default_context", lambda: client_tls)
        yield state
        server.shutdown()
        thread.join()


@pytest.fixture
def calendar_server(credentials, monkeypatch):
    state = {"requests": [], "status": 200, "token_status": 200, "pages": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply()

        def do_POST(self):
            self.reply()

        def reply(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            state["requests"].append((self.command, self.path, body, self.headers.get("Authorization")))
            token = self.path == "/token"
            self.send_response(state["token_status"] if token else state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {"access_token": "test-access"} if token else {"id": "event-1", **(json.loads(body) if body else {})}
            if self.command == "GET":
                data = {"items": [{"id": "event-2" if "pageToken=" in self.path else "event-1", "summary": "Tea"}]}
                if state["pages"] and "pageToken=" not in self.path:
                    data["nextPageToken"] = "second"
            self.wfile.write(json.dumps(data).encode())

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        monkeypatch.setattr(calendar, "TOKEN_URL", base + "/token")
        monkeypatch.setattr(calendar, "API_URL", base)
        yield state
        server.shutdown()
        thread.join()


def test_smtp_starttls_and_unicode(mail_server):
    result = asyncio.run(mail.send_mail("friend@example.test", "Grüße", "Bis später.\n.Line"))
    assert result["status"] == "sent" and result["message_id"]
    assert mail_server["commands"].index("STARTTLS") < mail_server["commands"].index("AUTH")
    msg = mail.BytesParser(policy=mail.policy.default).parsebytes(mail_server["messages"][0])
    assert str(msg["Subject"]) == "Grüße" and "Bis später." in msg.get_content()


def test_partial_recipients(mail_server):
    mail_server["refuse"] = True
    result = asyncio.run(mail.send_mail("ok@example.test, bad@example.test", "Hello", "Body"))
    assert result["status"] == "partially_sent"
    assert result["accepted"] == ["ok@example.test"] and result["refused"] == ["bad@example.test"]


def test_auth_failure_is_sanitized(mail_server):
    mail_server["fail"] = "auth"
    with pytest.raises(RuntimeError, match="could not confirm") as caught:
        asyncio.run(mail.send_mail("friend@example.test", "Hello", "Body"))
    assert "test-secret" not in str(caught.value)
    assert not mail_server["messages"]


@pytest.mark.parametrize("recipient", ["", "invalid", "@host", "name@", "a@b\r\nBcc: c@d"])
def test_bad_recipient(credentials, recipient):
    with pytest.raises(ValueError):
        asyncio.run(mail.send_mail(recipient, "Hello", "Body"))


def test_header_injection(credentials):
    with pytest.raises(ValueError):
        asyncio.run(mail.send_mail("a@b.test", "Hi\r\nBcc: x@y.test", "Body"))


def test_imap_readonly_latest_and_decoding(imap_server):
    result = asyncio.run(mail.read_inbox(1))
    assert result["messages"][0]["uid"] == "9"
    assert result["messages"][0]["subject"] == "Grüße"
    assert "EXAMINE INBOX" in imap_server["commands"]
    assert any("BODY.PEEK[]" in c for c in imap_server["commands"])


def test_imap_literal_search(imap_server):
    query = 'Grüße "quoted" \\ ALL'
    assert len(asyncio.run(mail.read_inbox(10, query))["messages"]) == 2
    assert imap_server["query"] == query.encode()


def test_imap_empty(imap_server):
    imap_server["empty"] = True
    assert asyncio.run(mail.read_inbox())["messages"] == []


@pytest.mark.parametrize("stage", ["select", "search", "fetch"])
def test_imap_errors(imap_server, stage):
    imap_server["fail"] = stage
    with pytest.raises(RuntimeError):
        asyncio.run(mail.read_inbox())


@pytest.mark.parametrize("count", [0, -1, True, 1.2, "2"])
def test_bad_count(count):
    with pytest.raises(ValueError):
        asyncio.run(mail.read_inbox(count))


@pytest.mark.parametrize("query", ["x\r\nLOGOUT", "x\x00", 5])
def test_bad_query(query):
    with pytest.raises(ValueError):
        asyncio.run(mail.read_inbox(1, query))


def test_calendar_refresh_create(calendar_server):
    result = asyncio.run(calendar.create_event("Tea", "2026-09-14T12:00:00+02:00", "2026-09-14T13:00:00+02:00"))
    assert result["id"] == "event-1" and result["summary"] == "Tea"
    token, request = calendar_server["requests"]
    assert b"grant_type=refresh_token" in token[2]
    assert request[3] == "Bearer test-access"
    assert json.loads(request[2])["start"]["dateTime"].endswith("+02:00")


@pytest.mark.parametrize("day,offsets", [("2026-03-29", ("+01:00", "+02:00")), ("2026-10-25", ("+02:00", "+01:00")),
                                        ("2026-09-13", ("+02:00", "+02:00"))])
def test_calendar_day_boundaries(calendar_server, day, offsets):
    from urllib.parse import parse_qs, urlsplit
    result = asyncio.run(calendar.list_events(day))
    params = parse_qs(urlsplit(calendar_server["requests"][1][1]).query)
    assert params["timeMin"][0].endswith(offsets[0]) and params["timeMax"][0].endswith(offsets[1])
    assert params["singleEvents"] == ["true"] and result["events"][0]["id"] == "event-1"


def test_calendar_pagination(calendar_server):
    calendar_server["pages"] = True
    assert [e["id"] for e in asyncio.run(calendar.list_events("2026-09-13"))["events"]] == ["event-1", "event-2"]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
@pytest.mark.parametrize("operation", ["read", "create"])
def test_calendar_errors_no_retry(calendar_server, status, operation):
    calendar_server["status"] = status
    with pytest.raises(RuntimeError):
        asyncio.run(calendar.list_events("2026-09-13") if operation == "read" else
                    calendar.create_event("Tea", "2026-09-14T12:00:00Z", "2026-09-14T13:00:00Z"))
    assert len(calendar_server["requests"]) == 2


def test_refresh_failure(calendar_server):
    calendar_server["token_status"] = 400
    with pytest.raises(RuntimeError, match="Reconnect"):
        asyncio.run(calendar.list_events("2026-09-13"))
    assert len(calendar_server["requests"]) == 1


@pytest.mark.parametrize("title,start,end", [("", "2026-09-14T12:00Z", "2026-09-14T13:00Z"),
    ("Tea", "2026-09-14T12:00", "2026-09-14T13:00"), ("Tea", "2026-09-14T13:00Z", "2026-09-14T12:00Z"),
    ("Tea", "2026-09-14T12:00Z", "2026-09-14T12:00Z"), ("Tea", "garbage", "garbage")])
def test_calendar_invalid_event(title, start, end):
    with pytest.raises(ValueError):
        asyncio.run(calendar.create_event(title, start, end))


@pytest.mark.parametrize("operation,key", [("mail", "OLA_MAIL_USER"), ("mail", "OLA_MAIL_APP_PASSWORD"),
    ("calendar", "OLA_GOOGLE_CLIENT_ID"), ("calendar", "OLA_GOOGLE_CLIENT_SECRET"), ("calendar", "OLA_GOOGLE_REFRESH_TOKEN")])
def test_missing_credentials(credentials, monkeypatch, operation, key):
    monkeypatch.delenv(key)
    with pytest.raises(ValueError, match="not connected"):
        asyncio.run(mail.read_inbox() if operation == "mail" else calendar.list_events("2026-09-13"))
