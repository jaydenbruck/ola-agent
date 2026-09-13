"""SMTP submission and read-only IMAP. Credentials stay at the connection boundary."""
import asyncio
import imaplib
import os
import smtplib
import ssl
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid


def _settings():
    user, password = os.getenv("OLA_MAIL_USER"), os.getenv("OLA_MAIL_APP_PASSWORD")
    if not user or not password:
        raise ValueError("Mail is not connected.")
    return user, password


def _send(to, subject, body):
    user, password = _settings()
    if not isinstance(to, str) or any(c in to for c in "\r\n"):
        raise ValueError("Enter a valid recipient.")
    addresses = [address for _, address in getaddresses([to])]
    if not addresses or any("@" not in a or a.startswith("@") or a.endswith("@") for a in addresses):
        raise ValueError("Enter a valid recipient.")
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = user, to, subject
    message["Date"], message["Message-ID"] = formatdate(localtime=False), make_msgid()
    message.set_content(body)
    try:
        with smtplib.SMTP(os.getenv("OLA_MAIL_HOST", "smtp.gmail.com"),
                          int(os.getenv("OLA_MAIL_PORT", "587")), timeout=30) as client:
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
            client.login(user, password)
            refused = client.send_message(message, from_addr=user, to_addrs=addresses)
    except (OSError, smtplib.SMTPException):
        # A disconnect after DATA has an unknown outcome. Never automatically resend.
        raise RuntimeError("Mail could not confirm the send. Check sent mail before retrying.") from None
    return {"status": "partially_sent" if refused else "sent", "message_id": message["Message-ID"],
            "accepted": [a for a in addresses if a not in refused], "refused": list(refused)}


async def send_mail(to: str, subject: str, body: str):
    """Submit once, with STARTTLS required. Sent means accepted by the mail server."""
    return await asyncio.to_thread(_send, to, subject, body)


def _read(n, query):
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("The message count must be a positive integer.")
    if query is not None and (not isinstance(query, str) or any(c in query for c in "\r\n\x00")):
        raise ValueError("Enter a single-line search.")
    user, password = _settings()
    host = os.getenv("OLA_MAIL_IMAP_HOST") or os.getenv("OLA_MAIL_HOST", "imap.gmail.com").replace("smtp.", "imap.", 1)
    try:
        with imaplib.IMAP4_SSL(host, int(os.getenv("OLA_MAIL_IMAP_PORT", "993")),
                              ssl_context=ssl.create_default_context(), timeout=30) as client:
            client.login(user, password)
            if client.select("INBOX", readonly=True)[0] != "OK":
                raise RuntimeError("The inbox could not be opened.")
            # Treat query as literal TEXT, never as an IMAP command fragment.
            if query:
                client.literal = query.encode("utf-8")
                status, data = client.uid("search", "CHARSET", "UTF-8", "TEXT")
            else:
                status, data = client.uid("search", None, "ALL")
            if status != "OK":
                raise RuntimeError("The inbox search failed.")
            messages = []
            for uid in reversed((data[0] or b"").split()[-n:]):
                status, fetched = client.uid("fetch", uid, "(BODY.PEEK[])")
                if status != "OK":
                    raise RuntimeError("A message could not be read.")
                raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
                if raw is None:
                    continue  # Message removed between search and fetch.
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                part = msg.get_body(preferencelist=("plain", "html"))
                messages.append({"uid": uid.decode(), "message_id": str(msg.get("Message-ID", "")),
                                 "from": str(msg.get("From", "")), "subject": str(msg.get("Subject", "")),
                                 "date": str(msg.get("Date", "")), "body": part.get_content() if part else ""})
            return {"messages": messages}
    except (OSError, imaplib.IMAP4.error, UnicodeError):
        raise RuntimeError("Mail could not read the inbox. Check the connection and try again.") from None


async def read_inbox(n: int = 10, query: str | None = None):
    """Read newest messages without marking them read. Query is a literal text search."""
    return await asyncio.to_thread(_read, n, query)


TOOLS = [
    {"type": "function", "function": {"name": "send_mail", "description": "Send an email to the requested recipients.",
     "parameters": {"type": "object", "properties": {"to": {"type": "string"}, "subject": {"type": "string"},
     "body": {"type": "string"}}, "required": ["to", "subject", "body"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_inbox", "description": "Read newest inbox messages; optionally search literal text.",
     "parameters": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1, "default": 10},
     "query": {"type": "string"}}, "additionalProperties": False}}},
]
HANDLERS = {"send_mail": send_mail, "read_inbox": read_inbox}
