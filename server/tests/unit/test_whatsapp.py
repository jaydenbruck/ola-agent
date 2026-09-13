"""Drive the WhatsApp wrapper through real Chromium on an isolated local page."""
import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from ola.tools import whatsapp

PAGE = """<!doctype html><html lang=en><title>Local WhatsApp test</title><body>
<div id=side><div role=textbox contenteditable=true aria-label=Search></div>
<div id=pane-side><div title=Alex role=button>Alex</div><div title=Sam role=button>Sam</div></div></div>
<div id=main hidden><header><span title=Alex>Alex</span></header><section id=messages>
<div class=message-in data-id=old-in><span class=selectable-text>Hello Alex</span></div>
<div class=message-out data-id=old-out><span class=selectable-text>Earlier message</span><i data-icon=msg-check></i></div>
</section><footer><div role=textbox contenteditable=true aria-label=Message></div><button aria-label=Send>Send</button></footer></div>
<script>(()=>{
window.sends=0; window.mode='sent';
const search=document.querySelector('#side [role=textbox]');
search.addEventListener('input',()=>{for(const e of document.querySelectorAll('#pane-side [title]'))e.hidden=!e.title.toLowerCase().includes(search.innerText.toLowerCase())});
document.querySelector('#pane-side').addEventListener('click',e=>{
 if(!e.target.title)return;document.querySelector('#main').hidden=false;
 const h=document.querySelector('#main header span');h.title=e.target.title;h.textContent=e.target.title;
});
document.querySelector('#main footer button').onclick=()=>{
 window.sends++;const text=document.querySelector('#main footer [role=textbox]').innerText;
 if(window.mode==='no-new-message')return;
 const e=document.createElement('div');e.className='message-out';e.dataset.id='new-'+window.sends;
 const t=document.createElement('span');t.className='selectable-text';t.innerText=text;e.append(t);
 if(window.mode!=='queued'){const i=document.createElement('i');i.dataset.icon='msg-check';e.append(i)}
 document.querySelector('#messages').append(e);document.querySelector('#main footer [role=textbox]').innerText='';
};
})();</script></body></html>"""


@pytest_asyncio.fixture
async def bridge(monkeypatch):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.route("https://web.whatsapp.com/**", lambda route: route.fulfill(body=PAGE, content_type="text/html"))
        calls = []

        async def page_for_job(job_id, lang="de"):
            return page

        async def run(job_id, args, lang="de"):
            calls.append(dict(args))
            if args["action"] == "goto":
                await page.goto(args["url"])
            return SimpleNamespace(ok=True, text=await page.locator("body").inner_text(), image=await page.screenshot(type="jpeg"),
                                   step=args["action"], needs_you={"reason": args["reason"], "url": page.url} if args["action"] == "needs_you" else None)

        page_lock = asyncio.Lock()
        adapter = SimpleNamespace(page_for_job=page_for_job, run=run, page=page, calls=calls, job_lock=lambda _: page_lock)
        monkeypatch.setattr(whatsapp, "_bridge", lambda: adapter)
        monkeypatch.setattr(whatsapp, "_lock", asyncio.Lock())
        monkeypatch.setattr(whatsapp, "WAIT_MS", 300)
        # Receipt polling is tested without waiting 15 seconds for known local failures.
        original_sleep = asyncio.sleep
        async def quick_sleep(seconds):
            await original_sleep(0)
        monkeypatch.setattr(whatsapp.asyncio, "sleep", quick_sleep)
        yield adapter
        await browser.close()


@pytest.mark.asyncio
async def test_whatsapp_send_confirms_new_receipt(bridge):
    result = await whatsapp.send_message("Alex", "Grüße\nBis später!", lang="de")
    assert result.ok and json.loads(result.text)["status"] == "sent"
    assert json.loads(result.text)["message_id"] == "new-1"
    assert await bridge.page.evaluate("window.sends") == 1
    assert sum(c["action"] == "read" for c in bridge.calls) >= 4


@pytest.mark.asyncio
async def test_whatsapp_read_loaded_messages(bridge):
    result = await whatsapp.read_chat("Alex")
    content = json.loads(result.text)
    assert content["scope"] == "currently loaded messages"
    assert content["messages"][0]["text"] == "Hello Alex"
    assert await bridge.page.evaluate("window.sends") == 0


@pytest.mark.asyncio
async def test_whatsapp_read_chat_list(bridge):
    result = await whatsapp.read_chat()
    assert "Alex" in json.loads(result.text)["chats"]
    assert await bridge.page.evaluate("window.sends") == 0


@pytest.mark.asyncio
async def test_whatsapp_qr_requests_takeover(bridge):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.set_content('<canvas aria-label="Scan this QR code to link a device!"></canvas>')
    result = await whatsapp.send_message("Alex", "Hello")
    assert result.needs_you and "QR-Code" in result.needs_you["reason"]
    assert bridge.calls[-1]["action"] == "needs_you"


@pytest.mark.asyncio
async def test_whatsapp_resume_uses_same_page(bridge):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.set_content('<canvas aria-label="Scan this QR code to link a device!"></canvas>')
    first = await whatsapp.read_chat("Alex")
    assert first.needs_you
    await bridge.page.set_content(PAGE)
    second = await whatsapp.read_chat("Alex")
    assert second.ok and not second.needs_you
    assert not any(c["action"] == "goto" for c in bridge.calls)


@pytest.mark.asyncio
async def test_whatsapp_duplicate_contact_does_not_send(bridge):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.evaluate("document.querySelector('#pane-side').append(document.querySelector('[title=Alex]').cloneNode(true))")
    with pytest.raises(ValueError, match="Mehrere Kontakte"):
        await whatsapp.send_message("Alex", "Hello")
    assert await bridge.page.evaluate("window.sends") == 0


@pytest.mark.asyncio
async def test_whatsapp_missing_contact_does_not_send(bridge):
    result = await whatsapp.send_message("Unknown person", "Hello")
    assert not result.ok and "nichts gesendet" in result.text
    assert await bridge.page.evaluate("window.sends") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["queued", "no-new-message"])
async def test_whatsapp_unconfirmed_send_is_not_retried(bridge, mode):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.evaluate("mode => window.mode=mode", mode)
    result = await whatsapp.send_message("Alex", "Earlier message")
    assert not result.ok and "erneut sendest" in result.text
    assert await bridge.page.evaluate("window.sends") == 1


@pytest.mark.asyncio
async def test_whatsapp_changed_recipient_does_not_send(bridge):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.evaluate("document.querySelector('#main footer [role=textbox]').addEventListener('input',()=>document.querySelector('#main header span').title='Sam')")
    with pytest.raises(ValueError, match="geändert"):
        await whatsapp.send_message("Alex", "Hello")
    assert await bridge.page.evaluate("window.sends") == 0


@pytest.mark.asyncio
async def test_whatsapp_missing_send_control_does_not_send(bridge):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.locator("button").evaluate("e=>e.remove()")
    with pytest.raises(ValueError, match="Sendetaste"):
        await whatsapp.send_message("Alex", "Hello")
    assert await bridge.page.evaluate("window.sends") == 0


@pytest.mark.asyncio
async def test_whatsapp_unready_page_is_not_linked(bridge):
    await bridge.page.goto(whatsapp.HOME)
    await bridge.page.set_content("<p>Loading</p>")
    result = await whatsapp.read_chat(lang="en")
    assert not result.ok and not result.needs_you and "not ready" in result.text


@pytest.mark.asyncio
@pytest.mark.parametrize("contact,text", [("", "Hello"), (None, "Hello"), ("Alex", ""), ("Alex", None), ("Alex", 12)])
async def test_whatsapp_rejects_missing_message(contact, text):
    with pytest.raises(ValueError):
        await whatsapp.send_message(contact, text)


@pytest.mark.asyncio
async def test_whatsapp_emits_observed_steps(bridge):
    steps = []
    async def emit(result):
        assert result.image
        steps.append(result.step)
    await whatsapp.send_message("Alex", "Hello", emit=emit)
    assert len(steps) >= 5 and "gesendet" in steps[-1]
