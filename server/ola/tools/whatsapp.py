"""WhatsApp Web in the browser job's persistent session. No separate messaging client."""
import asyncio
import json
from urllib.parse import urlsplit

_lock = asyncio.Lock()
WAIT_MS = 15000
HOME = "https://web.whatsapp.com/"

# Read only rendered chat content. Receipt icons distinguish queued text from a send.
MESSAGES_JS = """() => [...document.querySelectorAll('#main .message-in, #main .message-out')].map(e => ({
  id: e.closest('[data-id]')?.getAttribute('data-id') || e.querySelector('[data-id]')?.getAttribute('data-id') || '',
  direction: e.classList.contains('message-out') ? 'outgoing' : 'incoming',
  text: [...e.querySelectorAll('.selectable-text')].map(n => n.innerText).join('\\n'),
  receipt: [...e.querySelectorAll('[data-icon]')].map(n => n.getAttribute('data-icon')).find(v => /^(msg-check|msg-dblcheck|msg-dblcheck-ack)$/.test(v)) || ''
}))"""


def _bridge():
    from . import browser
    return browser


def _words(lang, de, en):
    return de if lang.startswith("de") else en


async def _observed(browser, job_id, lang, step, emit=None):
    result = await browser.run(job_id, {"action": "read"}, lang=lang)
    result.step = step
    if emit:
        await emit(result)
    return result


async def _open(browser, job_id, lang, emit):
    page = await browser.page_for_job(job_id, lang=lang)
    if urlsplit(page.url).hostname != "web.whatsapp.com":
        result = await browser.run(job_id, {"action": "goto", "url": HOME}, lang=lang)
        if emit:
            await emit(result)
        if not result.ok:
            return page, result
    # This wall is a request to link the member's account, never an account creation flow.
    try:
        async with browser.job_lock(job_id):
            await page.locator('#pane-side:visible, canvas[aria-label*="Scan"]:visible').first.wait_for(state="visible", timeout=WAIT_MS)
            linked = await page.locator("#pane-side").is_visible()
    except Exception:
        result = await _observed(browser, job_id, lang, _words(lang, "WhatsApp ist noch nicht bereit.", "WhatsApp is not ready yet."), emit)
        result.ok = False
        result.text = _words(lang, "WhatsApp ist noch nicht bereit. Die aktuelle Seite ist sichtbar.", "WhatsApp is not ready yet. The current page is visible.") + "\n" + result.text
        return page, result
    if not linked:
        # Founder ruling: use the DEVICE-CODE path, not the QR. Open "Link with phone number" and
        # hand over so the member enters THEIR OWN number; WhatsApp then shows an 8-char code that
        # browser.py reads and the app displays. Fall back to the QR only if the control is missing.
        took_phone = False
        try:
            async with browser.job_lock(job_id):
                took_phone = await browser.whatsapp_start_phone_link(page)
                if took_phone:
                    # Set the country to Germany and focus the number field FIRST, so the member
                    # never has to hit the tiny country flag (founder feedback). Country only.
                    await browser.whatsapp_set_germany(page)
        except Exception:
            took_phone = False
        if took_phone:
            reason = _words(lang,
                            "Tippe auf die Tastatur, gib deine Telefonnummer ein und drücke die Eingabetaste. WhatsApp "
                            "zeigt dann hier einen 8-stelligen Code, den du in WhatsApp unter 'Verknüpfte Geräte' eingibst.",
                            "Tap the keyboard, type your phone number, then press return. WhatsApp will then show an "
                            "8-character code here that you enter in WhatsApp under 'Linked Devices'.")
        else:
            reason = _words(lang, "Scanne bitte den QR-Code mit WhatsApp, um dein Konto zu verknüpfen.",
                            "Scan the QR code with WhatsApp to link your account.")
        return page, await browser.run(job_id, {"action": "needs_you", "reason": reason}, lang=lang)
    return page, None


async def _contact(page, browser, job_id, lang, contact, emit):
    search = page.locator('#side [contenteditable="true"][role="textbox"], #side [role="searchbox"], #side input[type="text"]').first
    async with browser.job_lock(job_id):
        await search.fill(contact, timeout=WAIT_MS)
    await _observed(browser, job_id, lang, _words(lang, f"Ich suche {contact} in WhatsApp.", f"I'm finding {contact} in WhatsApp."), emit)
    matches = page.locator("#pane-side").get_by_title(contact, exact=True)
    async with browser.job_lock(job_id):
        await matches.first.wait_for(state="visible", timeout=WAIT_MS)
        if await matches.count() != 1:
            raise ValueError(_words(lang, "Mehrere Kontakte haben diesen Namen. Welchen meinst du?", "Several contacts have this name. Which one do you mean?"))
        await matches.click()
        header = page.locator("#main header").get_by_title(contact, exact=True)
        await header.first.wait_for(state="visible", timeout=WAIT_MS)
    await _observed(browser, job_id, lang, _words(lang, f"Der Chat mit {contact} ist offen.", f"The chat with {contact} is open."), emit)


async def _execute(contact, text, job_id, lang, emit):
    if contact is not None and (not isinstance(contact, str) or not contact.strip()):
        raise ValueError(_words(lang, "Nenne einen Kontakt.", "Name a contact."))
    if text is not None and (not isinstance(text, str) or not text.strip()):
        raise ValueError(_words(lang, "Die Nachricht ist leer.", "The message is empty."))
    browser = _bridge()
    async with _lock:
        page, stopped = await _open(browser, job_id, lang, emit)
        if stopped:
            return stopped
        attempted = False
        try:
            if contact:
                await _contact(page, browser, job_id, lang, contact, emit)
            if text is None:
                async with browser.job_lock(job_id):
                    if contact:
                        content = {"contact": contact, "messages": await page.evaluate(MESSAGES_JS), "scope": "currently loaded messages"}
                    else:
                        content = {"chats": await page.locator("#pane-side").inner_text(), "scope": "currently visible chat list"}
                result = await _observed(browser, job_id, lang, _words(lang, "Ich habe WhatsApp gelesen.", "I've read WhatsApp."))
                result.text = json.dumps(content, ensure_ascii=False)
                return result
            composer = page.locator('#main footer [contenteditable="true"][role="textbox"]').first
            async with browser.job_lock(job_id):
                before = await page.evaluate(MESSAGES_JS)
                seen = {m["id"] for m in before if m["id"]}
                await composer.fill(text, timeout=WAIT_MS)
                if (await composer.inner_text()).replace("\r\n", "\n") != text.replace("\r\n", "\n"):
                    raise ValueError(_words(lang, "Der Nachrichtentext wurde nicht vollständig übernommen.", "The message text was not entered completely."))
            await _observed(browser, job_id, lang, _words(lang, "Die Nachricht steht im Chatfeld.", "The message is in the chat field."), emit)
            # Verify recipient immediately before the irreversible click.
            async with browser.job_lock(job_id):
                if not await page.locator("#main header").get_by_title(contact, exact=True).first.is_visible():
                    raise ValueError(_words(lang, "Der offene Chat hat sich geändert. Es wurde nichts gesendet.", "The open chat changed. Nothing was sent."))
                send = page.locator('#main footer button[aria-label="Send"], #main footer button[aria-label="Senden"], #main footer [role="button"][aria-label="Send"], #main footer [role="button"][aria-label="Senden"]')
                if await send.count() != 1:
                    raise ValueError(_words(lang, "Die Sendetaste ist nicht eindeutig erkennbar.", "The send button is not uniquely identifiable."))
                attempted = True
                await send.click(timeout=WAIT_MS)
            # A new message ID plus a server receipt is stronger than the text merely appearing.
            for _ in range(30):
                async with browser.job_lock(job_id):
                    messages = await page.evaluate(MESSAGES_JS)
                confirmed = next((m for m in messages if m["direction"] == "outgoing" and m["id"] and
                                  m["id"] not in seen and m["text"] == text and m["receipt"]), None)
                if confirmed:
                    result = await _observed(browser, job_id, lang, _words(lang, f"Die Nachricht an {contact} wurde gesendet.", f"The message to {contact} was sent."))
                    result.text = json.dumps({"status": "sent", "contact": contact, "text": text,
                                              "message_id": confirmed["id"], "receipt": confirmed["receipt"]}, ensure_ascii=False)
                    return result
                await asyncio.sleep(0.5)
        except ValueError:
            raise
        except Exception:
            pass  # Never expose page internals or retry a send after an uncertain click.
        result = await _observed(browser, job_id, lang, _words(lang, "Ich konnte den Vorgang nicht bestätigen.", "I could not confirm the action."))
        result.ok = False
        result.text = _words(lang, "Die Nachricht könnte gesendet sein. Lies den Chat, bevor du erneut sendest.",
                             "The message may have been sent. Read the chat before sending again.") if attempted else _words(
                                 lang, "Der Chat konnte nicht sicher geöffnet oder vorbereitet werden. Es wurde nichts gesendet.",
                                 "The chat could not be opened or prepared reliably. Nothing was sent.")
        return result


async def send_message(contact: str, text: str, *, job_id: str = "whatsapp", lang: str = "de", emit=None):
    if not isinstance(contact, str) or not contact.strip() or not isinstance(text, str) or not text.strip():
        raise ValueError(_words(lang, "Nenne einen Kontakt und einen Nachrichtentext.", "Provide a contact and message text."))
    return await _execute(contact, text, job_id, lang, emit)


async def read_chat(contact: str | None = None, *, job_id: str = "whatsapp", lang: str = "de", emit=None):
    return await _execute(contact, None, job_id, lang, emit)


async def _send(args, ctx):
    return await send_message(**args, job_id=ctx.job_id, lang=ctx.lang, emit=_progress(ctx))


async def _read(args, ctx):
    return await read_chat(**args, job_id=ctx.job_id, lang=ctx.lang, emit=_progress(ctx))


def _progress(ctx):
    async def emit(result):
        if ctx.agent is None:
            return
        job = ctx.agent.jobs.get(ctx.job_id)
        if job:
            job.last_step = result.step
        event = {"type": "job.step", "job_id": ctx.job_id, "text": result.step}
        frame = _bridge().frame_url(ctx.job_id)
        if frame:
            event["frame_url"] = frame
        ctx.agent.bus.emit(ctx.thread_id, event)
    return emit


TOOLS = [
    {"type": "function", "function": {"name": "send_message", "description": "Send the requested message to an exact WhatsApp contact name; ask for QR takeover if unlinked.",
     "parameters": {"type": "object", "properties": {"contact": {"type": "string"}, "text": {"type": "string"}},
     "required": ["contact", "text"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_chat", "description": "Read loaded messages for an exact WhatsApp contact name, or the visible chat list if omitted.",
     "parameters": {"type": "object", "properties": {"contact": {"type": "string"}}, "additionalProperties": False}}},
]
HANDLERS = {"send_message": _send, "read_chat": _read}
