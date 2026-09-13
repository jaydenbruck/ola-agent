"""Ola's browser: Playwright Chromium, headless, a phone-sized viewport, one persistent profile
so a sign-in survives a restart, one page per job.

What the model gets after every action is the page as a short text it can act on: the title and
address, the facts Ola knows about that site, the interactive elements with stable refs (`e12`)
grouped by region (dialog, header, main, footer), a short excerpt of the visible text, plus a
JPEG of the viewport as an image part. Actions act on refs. A failed action returns why and the
fresh refs, so the model chooses again instead of blaming the site.

`needs_you(reason)` pauses the job: the two takeover routes serve the page's frames and accept
the member's taps and typing until the job is resumed.

The tool knows nothing about events, threads or the model; it needs a job id, the member's
language and (for uploads) a way to find an attachment on disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

log = logging.getLogger("ola.browser")

try:
    from fastapi import Depends, HTTPException, Request, Response
except ImportError:  # pragma: no cover  the tool works without FastAPI; only mount() needs it
    Depends = HTTPException = Request = Response = None  # type: ignore[assignment]

VIEWPORT = {"width": 390, "height": 844}  # CSS pixels: the phone's takeover view and its tap coordinates
DEVICE_SCALE = 3  # frames are 1170x2532 device pixels, sharp enough for a QR code on the phone
FRAME_QUALITY = 90  # the JPEG the phone shows
MODEL_QUALITY = 45  # the small JPEG the model sees (390x844, detail low)
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.6778.33 Mobile Safari/537.36"
)
PROFILE_DIR = Path(os.environ.get("OLA_PROFILE_DIR") or Path(__file__).resolve().parents[2] / ".profile")
ACTION_BUDGET_S = 30.0  # the most any single action may take
SETTLE_BUDGET_S = 8.0  # how long a page may take to become ready after an action
PICK_WAIT_S = 6.0  # how long a suggestion list may take to open
MAX_ELEMENTS = 60  # the element list the model sees is capped here
FOOTER_LINES = 6
TEXT_EXCERPT = 700  # characters of page text in a snapshot; `read` gives more
READ_LIMIT = 8000

ACTIONS = ("goto", "click", "fill", "pick", "press", "scroll", "read", "wait_for", "dismiss_dialog", "upload", "needs_you")

TOOL: dict[str, Any] = {
    "name": "browser",
    "description": (
        "Drive a real phone-sized browser. Every call returns the page: its elements with refs like e12 "
        "grouped by region (dialog, header, main, footer), a text excerpt and a screenshot. Act on refs. "
        "Actions: goto(url) · click(ref) · fill(fields=[{ref,text},...]) several fields at once · "
        "pick(ref, text) for address and suggestion fields (types, waits for the list, taps the matching row, "
        "checks the field shows it) · press(key) · scroll(dy) · read() the whole page text · "
        "wait_for(text or url, seconds) · dismiss_dialog() closes a cookie banner or modal, rejecting where it can · "
        "upload(ref, attachment_id) into a file input · needs_you(reason) when the page asks for a password, "
        "a code or a captcha: the member takes over that page and you continue after. "
        "When a dialog is open, clear it first. Never fill a sign-up or join page. A failed action tells you why "
        "and gives fresh refs: choose again, the site is fine."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "url": {"type": "string", "description": "goto: the address to open"},
            "ref": {"type": "string", "description": "click, pick, upload: the element ref, e.g. e12"},
            "text": {"type": "string", "description": "pick: what to type; wait_for: the text to wait for"},
            "fields": {
                "type": "array",
                "description": "fill: the fields to fill, in order",
                "items": {
                    "type": "object",
                    "properties": {"ref": {"type": "string"}, "text": {"type": "string"}},
                    "required": ["ref", "text"],
                },
            },
            "key": {"type": "string", "description": "press: Enter, Escape, Tab, ArrowDown, Backspace, ..."},
            "dy": {"type": "integer", "description": "scroll: pixels down (negative = up); default 600"},
            "seconds": {"type": "number", "description": "wait_for: budget in seconds, at most 30"},
            "attachment_id": {"type": "string", "description": "upload: the attachment to put into the file input"},
            "reason": {"type": "string", "description": "needs_you: why, in the member's language"},
        },
        "required": ["action"],
    },
}

# Facts about the sites Ola visits today. Facts, never scripts: the model still drives.
SITE_FACTS: dict[str, dict[str, str]] = {
    "uber.com": {
        "sign_in": "https://auth.uber.com/v2/",
        "cookie_reject": "Ablehnen",
        "facts": (
            "Fares without a sign-in: https://www.uber.com/global/de/price-estimate/ ; signed in: https://m.uber.com/go/home. "
            "'Abholort' (pickup) and 'Ziel' (destination) are suggestion pickers: use pick with the full street address "
            "and check the field then shows the picked place; typing alone leaves it empty. A place is always an address, "
            "never coordinates. Fares appear once both fields are set."
        ),
    },
    "lieferando.de": {
        "sign_in": "https://www.lieferando.de/login",
        "cookie_reject": "Nur notwendige",
        "facts": (
            "The customer sign-in is 'Anmelden' in the header, or https://www.lieferando.de/login. The footer's "
            "'Ein Restaurant anmelden' / partner links are the restaurant sign-up: never the way in. To order: open the "
            "restaurant list for the place directly, https://www.lieferando.de/lieferservice/essen/<ort>-<plz> (e.g. dreieich-63303); "
            "the start page's 'Ort suchen' panel is slow. Close the app-download dialog ('Schließen'). Open a restaurant "
            "(links go to /speisekarte/...), tap the dish, add it to the cart ('In den Warenkorb'), stop before payment; the "
            "exact delivery address is asked at checkout."
        ),
    },
    "kleinanzeigen.de": {
        "sign_in": "https://www.kleinanzeigen.de/m-einloggen.html",
        "cookie_reject": "Alle ablehnen",
        "facts": (
            "Searching needs no sign-in: the fields 'Was suchst du?' and 'PLZ oder Ort' on the start page, or open "
            "https://www.kleinanzeigen.de/s-<ort>/<suchbegriff>/k0 directly. A listing lives at kleinanzeigen.de/s-anzeige/..."
        ),
    },
    "web.whatsapp.com": {
        "sign_in": "https://web.whatsapp.com/",
        "facts": (
            "WhatsApp Web. A page showing a QR code ('Mit Telefon verknüpfen' / 'Link with phone') is the sign-in: use "
            "needs_you so the member scans it with their phone; never try to type there. Once linked: the chat list is on the "
            "left with the search box 'Suchen oder neuen Chat beginnen' at the top; type the contact's name there and click the "
            "matching chat row. The message composer is the text box at the bottom of the open chat ('Nachricht eingeben'); "
            "type the message, then press Enter or click the send button. A sent message appears at the bottom of the chat with "
            "ticks. Never send to a contact that did not match the name exactly."
        ),
    },
    "linkedin.com": {
        "sign_in": "https://www.linkedin.com/login",
        "facts": (
            "Sign in only at https://www.linkedin.com/login. A page titled 'Jetzt Mitglied werden' or a /signup address is "
            "the join funnel: never fill it. A profile is linkedin.com/in/<name>; messages open from the profile's 'Nachricht' button."
        ),
    },
}

# Sites that only work as a desktop page (WhatsApp Web sends a phone to the app store). Their
# pages get a desktop identity and a wider viewport; frames and taps then use that page's size.
DESKTOP_SITES = {"web.whatsapp.com"}
DESKTOP_VIEWPORT = {"width": 780, "height": 1688}  # the same 390:844 shape at half scale: frames stay 1170x2532, a tap maps by x2
# What a site shows once it is really there (its splash screen is not a page): waited for after goto, bounded.
READY_HINTS = {"web.whatsapp.com": "canvas, #pane-side, [aria-label*='Chatliste' i], [aria-label*='chat list' i], [data-testid*='chat-list']"}
DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.6778.33 Safari/537.36"

_JOIN_URL = re.compile(r"/(signup|sign-up|join|register|registrieren|cold-join)(/|\?|\.|$)", re.I)
_REGIONS = ("dialog", "header", "main", "footer")
_LOGIN_WALL = re.compile(r"melde dich an|anmelden, um|einloggen, um|log ?in to|sign ?in to|bitte anmelden|please (sign|log) in|wie lautet deine telefonnummer", re.I)


@dataclass
class BrowserResult:
    text: str
    image: Optional[bytes]
    step: str
    ok: bool = True
    needs_you: Optional[dict[str, str]] = None
    url: str = ""
    code: Optional[str] = None  # WhatsApp Web link code (XXXX-XXXX), when the code screen is showing
    code_hint: Optional[str] = None


# --------------------------------------------------------------------------- in-page scripts
# One script builds the snapshot. Refs live in a WeakMap on the DOM node, so a re-read gives the
# same ref for the same element; the counter is handed in so refs never repeat within a job.

SNAPSHOT_JS = r"""
(next) => {
  const S = window.__ola ||= {ids: new WeakMap(), next: 0, refs: {}};
  if (next > S.next) S.next = next;
  S.refs = {};
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };
  const secret = (el) => (el.type || '') === 'password' || /one-time-code|current-password|new-password/.test(el.autocomplete || '');
  const DIALOG = 'dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true], [id*=cookie i], [class*=cookie i], [id*=consent i], [class*=consent i], [id*=onetrust i], [id*=usercentrics i], [id*=didomi i], [class*=cmp-container i], [id*=cmp-container i], [class*=modal-open i], [class*=modal--open i], [class*=is-open i][class*=modal i]';
  const covers = (d) => {
    const r = d.getBoundingClientRect(); const s = getComputedStyle(d);
    return s.position === 'fixed' || s.position === 'sticky' || r.width * r.height > innerWidth * innerHeight * 0.05;
  };
  const hostOf = (n) => { const root = n.getRootNode && n.getRootNode(); return root && root.host ? root.host : null; };
  const dialogOf = (el) => {
    for (let n = el; n; n = hostOf(n)) {
      const d = n.closest ? n.closest(DIALOG) : null;
      if (d && vis(d) && covers(d)) return d;
    }
    return null;
  };
  const regionOf = (el) => {
    if (dialogOf(el)) return 'dialog';
    for (let n = el; n; n = hostOf(n)) {
      if (!n.closest) continue;
      if (n.closest('footer, [role=contentinfo], #footer, .footer, [class*=site-footer i], [class*=page-footer i]')) return 'footer';
      if (n.closest('header, [role=banner], nav, [role=navigation], #header, .header, [class*=site-header i], [class*=navbar i], [class*=topbar i]')) return 'header';
    }
    return 'main';
  };
  const label = (el) => {
    let t = clean(el.getAttribute('aria-label'));
    if (!t) { const ids = (el.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean); t = clean(ids.map(i => (document.getElementById(i) || {}).textContent || '').join(' ')); }
    if (!t && el.labels && el.labels.length) t = clean([...el.labels].map(l => l.innerText).join(' '));
    if (!t) t = clean(el.innerText || el.textContent);
    if (!t) t = clean(el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name'));
    if (!t && el.querySelector) { const im = el.querySelector('img[alt], svg title'); if (im) t = clean(im.getAttribute('alt') || im.textContent); }
    if (!t && el.tagName === 'INPUT' && !secret(el)) t = clean(el.value);
    return t.slice(0, 70);
  };
  const SEL = 'a[href], button, input, select, textarea, summary, [role=button], [role=link], [role=textbox], [role=searchbox], ' +
    '[role=combobox], [role=checkbox], [role=radio], [role=switch], [role=tab], [role=menuitem], [role=option], [contenteditable=true], [onclick]';
  const found = []; const asHost = new Set();
  const walk = (root, depth) => {
    for (const el of root.querySelectorAll(SEL)) found.push(el);
    if (depth > 2) return;
    for (const h of root.querySelectorAll('*')) {
      if (!h.shadowRoot) continue;
      if (h.tagName.includes('-') && clean(h.innerText) && h.shadowRoot.querySelector('button, a[href], input, [role=button], [role=link]')) { found.push(h); asHost.add(h); }
      walk(h.shadowRoot, depth + 1);
    }
  };
  walk(document, 0);
  const uniq = new Set(); const found1 = found.filter(el => !uniq.has(el) && uniq.add(el));
  found.length = 0; found.push(...found1);
  const PICKER_WORDS = /adresse|address|abhol|pickup|ziel|drop|destination|ort\b|stadt|city|plz|wohin|where|standort|location|street|straße|strasse/i;
  const out = []; let hasPassword = false, hasCode = false;
  for (const el of found) {
    if (!vis(el)) continue;
    if (el.closest && el.closest('[aria-hidden=true]')) continue;
    const h = hostOf(el); if (h && asHost.has(h)) continue;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && type === 'hidden') continue;
    if (tag === 'input' && type === 'password') hasPassword = true;
    if ((el.getAttribute('autocomplete') || '') === 'one-time-code') hasCode = true;
    let ref = S.ids.get(el);
    if (!ref) { ref = 'e' + (++S.next); S.ids.set(el, ref); }
    S.refs[ref] = el;
    const r = el.getBoundingClientRect();
    const role = el.getAttribute('role') || '';
    const item = {ref, tag: tag.includes('-') ? 'button' : tag, label: label(el), region: regionOf(el),
                  box: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
                  inView: r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth};
    if (role && role !== tag) item.role = role;
    if (type && tag === 'input') item.type = type;
    if (tag === 'a') { try { const u = new URL(el.href); item.href = (u.host + u.pathname).slice(0, 60); } catch (e) {} }
    if ((tag === 'input' || tag === 'textarea') && !secret(el) && el.value) item.value = String(el.value).slice(0, 40);
    if (tag === 'select') { item.value = clean((el.options[el.selectedIndex] || {}).text); item.options = [...el.options].slice(0, 10).map(o => clean(o.text).slice(0, 30)); }
    if (el.checked) item.checked = true;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') item.disabled = true;
    const ac = (el.getAttribute('aria-autocomplete') || '').toLowerCase();
    const hint = [el.getAttribute('name'), el.id, el.getAttribute('aria-label'), el.getAttribute('placeholder')].join(' ');
    if ((tag === 'input' && ['', 'text', 'search'].includes(type)) || role === 'combobox' || role === 'textbox' || role === 'searchbox') {
      if (role === 'combobox' || ac === 'list' || ac === 'both' || (el.getAttribute('aria-haspopup') || '') === 'listbox' || !!el.list || ((el.getAttribute('autocomplete') || '') === 'off' && PICKER_WORDS.test(hint))) item.picker = true;
    }
    if (item.picker && !item.value) {
      // a picker often shows its choice as a chip beside an emptied input: the words of the field's own box
      let box = el.parentElement;
      for (let i = 0; i < 2 && box; i++, box = box.parentElement) {
        if (box.querySelectorAll('input, select, textarea, button').length > 1) break;
        const shown = clean(box.innerText);
        if (shown && shown.length < 80 && shown.toLowerCase() !== item.label.toLowerCase()) {
          item.value = (shown.toLowerCase().startsWith(item.label.toLowerCase()) ? shown.slice(item.label.length) : shown).trim().slice(0, 40); break;
        }
      }
    }
    if (tag === 'input' && type === 'file') item.file = true;
    out.push(item);
  }
  let dialog = '';
  const first = out.find(i => i.region === 'dialog');
  if (first) { const d = dialogOf(S.refs[first.ref]); if (d) { const hd = d.querySelector('h1, h2, h3, [role=heading], strong'); dialog = clean(hd ? hd.innerText : d.innerText).slice(0, 100); } }
  const captcha = [...document.querySelectorAll('iframe[src*=recaptcha], iframe[src*=hcaptcha], iframe[src*=turnstile], iframe[src*=arkose], .g-recaptcha, .h-captcha, [id*=captcha i]')]
    .some(el => { const r = el.getBoundingClientRect(); return r.width > 60 && r.height > 60 && vis(el); });
  const text = clean(document.body ? document.body.innerText.replace(/\n{2,}/g, '\n') : '');
  // a bot-check interstitial (Cloudflare and friends): the page is not the site yet
  const challenge = /just a moment|nur einen moment|sicherheitsüberprüfung|checking your browser|verify you are human|attention required/i.test(document.title + ' ' + text.slice(0, 500));
  return {url: location.href, title: document.title, ready: document.readyState, elements: out, text, dialog, hasPassword, hasCode, captcha, challenge,
          scrollY: Math.round(scrollY), scrollH: Math.round(document.documentElement.scrollHeight), viewH: innerHeight, next: S.next};
}
"""

FULL_TEXT_JS = r"""() => (document.body ? document.body.innerText : '').replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim()"""

REF_BOX_JS = r"""(ref) => { const el = (window.__ola || {refs: {}}).refs[ref]; if (!el) return null; el.scrollIntoView({block: 'center', inline: 'center'}); const r = el.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height}; }"""

# The rows a suggestion list offers after typing, scored against what was typed; never a row that
# is not a place ("search for …", "use my location", the map pin).
SUGGESTIONS_JS = r"""
(typed) => {
  const norm = (s) => String(s || '').toLowerCase().replace(/ß/g, 'ss').replace(/stra(ss|ß)e/g, 'str').replace(/str\./g, 'str').replace(/[^a-z0-9äöü ]+/g, ' ').replace(/\s+/g, ' ').replace(/(\d+) ([a-z])(?= |$)/g, '$1$2').trim();
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 4 || r.height < 4) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const t = norm(typed); const words = t.split(' ').filter(w => w.length > 1);
  const af = document.activeElement;
  let rows = [];
  const Q = '[role=option], [role=listbox] li, [role=listbox] > *, ul[class*=suggest i] li, [class*=suggest i] li, [class*=suggest i] [role], li[class*=suggest i], ' +
            '[class*=autocomplete i] li, [class*=autocomplete i] [role], [id*=suggest i] li, [data-testid*=suggest i] li, [data-testid*=suggest i] > *, [class*=typeahead i] li, [class*=dropdown i] li, datalist option';
  const pull = (root, depth) => { for (const el of root.querySelectorAll(Q)) rows.push(el); if (depth > 2) return; for (const h of root.querySelectorAll('*')) if (h.shadowRoot) pull(h.shadowRoot, depth + 1); };
  pull(document, 0);
  rows = rows.filter(el => vis(el) && !el.closest('select') && !(af && el.contains(af)));
  if (!rows.length && af) {
    const fr = af.getBoundingClientRect(); const under = [];
    for (const el of document.querySelectorAll('li, [role=option], [role=menuitem], a, button, div[tabindex], div[class*=item i], div[class*=result i], div[class*=option i]')) {
      const b = el.getBoundingClientRect();
      if (b.width > 40 && b.height > 14 && b.height < 120 && b.top >= fr.bottom - 4 && b.top < fr.bottom + 520 && vis(el)) {
        const nt = norm(el.innerText); if (nt && nt.length < 200 && words.some(w => nt.includes(w))) under.push(el);
      }
    }
    rows = under;
  }
  rows = rows.filter(el => !rows.some(o => o !== el && el.contains(o)));
  const seen = new Set();
  rows = rows.filter(el => { const k = clean(el.innerText).slice(0, 120); if (!k || seen.has(k) || k.length > 220) return false; seen.add(k); return true; });
  if (!rows.length) return {open: false};
  const NOT_PLACE = /^(nach .* suchen|search (for|in) |.* suchen$|standort|use (my )?(current )?location|aktuellen standort|meinen standort|auf der karte|on the map|gespeicherte orte|saved places)/i;
  const num = words.find(w => /^\d+[a-z]?$/.test(w));
  const scored = rows.map(el => {
    const text = clean(el.innerText); const nt = norm(text); let s = 0;
    if (NOT_PLACE.test(text)) s -= 1000;
    if (t && nt.includes(t)) s += 100;
    for (const w of words) if (nt.includes(w)) s += /^\d+[a-z]?$/.test(w) ? 25 : 10;
    if (num && !new RegExp('(^|[^0-9])' + num + '([^0-9]|$)').test(nt)) s -= 40;
    return {el, text, s};
  });
  let best = null; for (const r of scored) if (!best || r.s > best.s) best = r;
  const listing = scored.slice(0, 8).map(r => r.text.slice(0, 90));
  if (!best || best.s <= 0) return {open: true, matched: false, rows: listing};
  best.el.scrollIntoView({block: 'nearest'});
  const r = best.el.getBoundingClientRect();
  return {open: true, matched: true, x: r.left + Math.min(r.width / 2, 160), y: r.top + r.height / 2, text: best.text.slice(0, 120), rows: listing};
}
"""

FIELD_SHOWS_JS = r"""
(ref) => {
  const el = (window.__ola || {refs: {}}).refs[ref]; if (!el) return '';
  if ((el.type || '') === 'password' || /one-time-code|current-password|new-password/.test(el.autocomplete || '')) return '';
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  let v = ('value' in el && el.value != null) ? clean(el.value) : clean(el.innerText || el.textContent);
  if (!v) {
    // a chip beside an emptied input: the words of the field's own box, without other controls in it
    const labels = [el.getAttribute('aria-label'), el.getAttribute('placeholder'), ...(el.labels ? [...el.labels].map(l => l.innerText) : [])].map(x => clean(x).toLowerCase()).filter(Boolean);
    let n = el.parentElement;
    for (let i = 0; i < 2 && n; i++, n = n.parentElement) {
      if (n.querySelectorAll('input, select, textarea, button').length > 1) break;
      let t = clean(n.innerText); if (t.length >= 120) break;
      for (const l of labels) if (t.toLowerCase().startsWith(l)) t = clean(t.slice(l.length));
      if (t) { v = t; break; }
    }
  }
  return v.slice(0, 160);
}
"""

# A cookie banner or modal out of the way: reject, else close, else accept. Returns where to tap.
DISMISS_JS = r"""
() => {
  const clean = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const DIALOG = 'dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true], [id*=cookie i], [class*=cookie i], [id*=consent i], [class*=consent i], [id*=onetrust i], [id*=usercentrics i], [id*=didomi i], [class*=cmp-container i], [id*=cmp-container i], [class*=modal-open i], [class*=modal--open i], [class*=is-open i][class*=modal i]';
  const covers = (d) => { const r = d.getBoundingClientRect(); const s = getComputedStyle(d); return s.position === 'fixed' || s.position === 'sticky' || r.width * r.height > innerWidth * innerHeight * 0.05; };
  const boxes = [];
  const collect = (root, depth) => { for (const el of root.querySelectorAll(DIALOG)) if (vis(el) && covers(el)) boxes.push(el); if (depth > 2) return; for (const h of root.querySelectorAll('*')) if (h.shadowRoot) collect(h.shadowRoot, depth + 1); };
  collect(document, 0);
  if (!boxes.length) return {found: false};
  const buttons = (c) => {
    const bs = [];
    const walk = (root, depth) => {
      for (const b of root.querySelectorAll('button, a, [role=button], input[type=button], input[type=submit]')) if (vis(b)) bs.push(b);
      if (depth > 2) return;
      for (const h of root.querySelectorAll('*')) { if (!h.shadowRoot) continue; if (h.tagName.includes('-') && vis(h) && clean(h.innerText) && h.shadowRoot.querySelector('button, a, [role=button]')) bs.push(h); walk(h.shadowRoot, depth + 1); }
    };
    walk(c, 0); return bs;
  };
  const words = (b) => clean(b.innerText || b.value || b.getAttribute('aria-label') || b.getAttribute('title')).toLowerCase();
  const REJECT = /^(nur (notwendige|erforderliche|essenzielle|technisch notwendige)( cookies)?( akzeptieren| zulassen| erlauben)?|(alle |alles |optionale )?ablehnen|reject( all)?( cookies)?|decline( all)?|refuse( all)?|(only |essential |necessary |strictly necessary )+(cookies )?(only)?|accept (only )?(necessary|essential|required)( cookies)?|nicht zustimmen|ohne zustimmung fortfahren|continue without (accepting|agreeing)|weiter ohne zustimmung)$/;
  const CLOSE = /^(schließen|schliessen|close|×|x|✕|dismiss|später|later|nein danke|no thanks|not now|jetzt nicht|verstanden|got it|ok|okay)$/;
  const ACCEPT = /^(alle akzeptieren|akzeptieren|alles akzeptieren|alle annehmen|annehmen|zustimmen|einverstanden|accept all|accept( cookies)?|agree|i agree|allow all|allow)$/;
  for (const c of boxes) {
    const bs = buttons(c);
    const pick = (re) => bs.find(b => re.test(words(b))) || bs.find(b => re.test((b.getAttribute('aria-label') || '').toLowerCase()));
    let b = pick(REJECT), kind = 'reject';
    if (!b) { b = pick(CLOSE); kind = 'close'; }
    if (!b) { b = pick(ACCEPT); kind = 'accept'; }
    if (b) { b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return {found: true, kind, clicked: (words(b) || kind).slice(0, 60), x: r.left + r.width / 2, y: r.top + r.height / 2, buttons: bs.map(words).filter(Boolean).slice(0, 8)}; }
  }
  return {found: true, kind: '', buttons: buttons(boxes[0]).map(words).filter(Boolean).slice(0, 8)};
}
"""

DIALOG_OPEN_JS = r"""
() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); if (r.width < 2 || r.height < 2) return false; const s = getComputedStyle(el); return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0'; };
  const DIALOG = 'dialog[open], [role=dialog], [role=alertdialog], [aria-modal=true], [id*=cookie i], [class*=cookie i], [id*=consent i], [class*=consent i], [id*=onetrust i], [id*=usercentrics i], [id*=didomi i], [class*=cmp-container i], [id*=cmp-container i], [class*=modal-open i], [class*=modal--open i], [class*=is-open i][class*=modal i]';
  const covers = (d) => { const r = d.getBoundingClientRect(); const s = getComputedStyle(d); return s.position === 'fixed' || s.position === 'sticky' || r.width * r.height > innerWidth * innerHeight * 0.05; };
  return [...document.querySelectorAll(DIALOG)].some(el => vis(el) && covers(el));
}
"""

WAIT_JS = r"""
([kind, want]) => {
  const w = String(want || '').toLowerCase();
  if (kind === 'url') return location.href.toLowerCase().includes(w);
  return ((document.body && document.body.innerText) || '').toLowerCase().includes(w);
}
"""


# --------------------------------------------------------------------------- sessions

class _Session:
    def __init__(self, job_id: str, page: Any, lang: str) -> None:
        self.job_id = job_id
        self.page = page
        self.lang = lang
        self.lock = asyncio.Lock()
        self.next_ref = 0
        self.frame: Optional[bytes] = None  # device pixels, for the phone
        self.model_image: Optional[bytes] = None  # 390x844, for the model
        self.frame_at = 0.0
        self.snap: dict[str, Any] = {}
        self.desktop = False
        self.desktop_forced = False  # set through page_for_job(desktop=...): goto then leaves the mode alone
        self.viewport = dict(VIEWPORT)


_pw: Any = None
_context: Any = None
_sessions: dict[str, _Session] = {}
_launch_lock: Optional[asyncio.Lock] = None


async def _ensure_context() -> Any:
    global _pw, _context, _launch_lock
    if _launch_lock is None:
        _launch_lock = asyncio.Lock()
    async with _launch_lock:
        if _context is not None:
            return _context
        from playwright.async_api import async_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        _pw = await async_playwright().start()
        _context = await _pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=os.environ.get("OLA_HEADED", "") not in ("1", "true", "yes"),
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE,
            is_mobile=True,
            has_touch=True,
            user_agent=MOBILE_UA,
            locale="de-DE",
            timezone_id="Europe/Berlin",
            ignore_default_args=["--enable-automation"],
            args=["--disable-blink-features=AutomationControlled", "--lang=de-DE"],
        )
        _context.set_default_timeout(int(ACTION_BUDGET_S * 1000))
        await _seed_cookies(_context)
        return _context


_SAME_SITE = {
    "strict": "Strict", "lax": "Lax", "none": "None",
    "no_restriction": "None", "unspecified": None, "": None,  # Chrome / extension exports
}


def _normalize_cookie(raw: Any) -> Optional[dict[str, Any]]:
    """One cookie in Playwright's add_cookies shape, from either that shape or a browser-extension
    export (EditThisCookie / Cookie-Editor: `expirationDate`, `sameSite: "no_restriction"`,
    `hostOnly`, `session`, `storeId`). Playwright rejects the extra keys and the Chrome sameSite
    values, so the file the founder exports must be translated or nothing seeds. Returns None for a
    cookie without a name or a value (a placeholder is not a session)."""
    if not isinstance(raw, dict):
        return None
    name, value = raw.get("name"), raw.get("value")
    if not name or value in (None, ""):
        return None
    out: dict[str, Any] = {"name": str(name), "value": str(value), "path": str(raw.get("path") or "/")}
    domain = raw.get("domain")
    if domain:
        out["domain"] = str(domain)
    elif raw.get("url"):
        out["url"] = str(raw["url"])
    else:
        return None  # add_cookies needs a url or a domain
    same = raw.get("sameSite")
    same = _SAME_SITE.get(str(same).lower(), same if same in ("Strict", "Lax", "None") else None)
    if same:
        out["sameSite"] = same
    out["secure"] = bool(raw.get("secure")) or same == "None"  # Chromium drops SameSite=None unless Secure
    if "httpOnly" in raw:
        out["httpOnly"] = bool(raw["httpOnly"])
    expires = raw.get("expires", raw.get("expirationDate"))
    if expires not in (None, "") and not raw.get("session"):
        try:
            out["expires"] = int(float(expires))
        except (TypeError, ValueError):
            pass
    return out


async def _seed_cookies(context: Any) -> None:
    """Seed session cookies from an operator-placed file (env OLA_SEED_COOKIES) so a site the
    founder cannot sign into inside the takeover (LinkedIn's clipped image-captcha) is already
    logged in. The file is a JSON list in Playwright's add_cookies shape or a browser-extension
    export, which is normalized (see `_normalize_cookie`). Each cookie is added on its own so one
    bad entry cannot sink a good `li_at`; add_cookies upserts by name+domain+path, so seeding every
    startup is idempotent and the persistent profile keeps them. Never logs a cookie's name, value
    or domain, only counts and the path; a bad or absent file never breaks startup. Whether a
    session cookie is still valid for the site is the site's call, not ours: this only places it."""
    seed = os.environ.get("OLA_SEED_COOKIES")
    if not seed or not Path(seed).is_file():
        return
    try:
        raw = json.loads(Path(seed).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001  a bad file is skipped, never fatal
        log.warning("cookie seed skipped, file not read: %s", type(e).__name__)
        return
    if isinstance(raw, dict) and isinstance(raw.get("cookies"), list):
        raw = raw["cookies"]  # some exports wrap the list in an object
    if not isinstance(raw, list) or not raw:
        log.warning("cookie seed skipped: %s holds no cookie list", seed)
        return
    cookies = [c for c in (_normalize_cookie(c) for c in raw) if c]
    added = 0
    for c in cookies:
        try:
            await context.add_cookies([c])
            added += 1
        except Exception as e:  # noqa: BLE001  a single bad cookie is skipped, the rest still seed
            log.warning("one cookie skipped: %s", type(e).__name__)
    try:
        present = {c["name"] for c in await context.cookies()}
        stuck = sum(1 for c in cookies if c["name"] in present)
    except Exception:  # noqa: BLE001
        stuck = added
    log.info("cookie seed from %s: %d in file, %d added, %d present after", seed, len(raw), added, stuck)


async def _session(job_id: str, lang: str = "de") -> _Session:
    s = _sessions.get(job_id)
    if s is not None and not s.page.is_closed():
        s.lang = lang or s.lang
        return s
    context = await _ensure_context()
    page = await context.new_page()
    s = _Session(job_id, page, lang or "de")
    page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

    def adopt(popup: Any) -> None:  # a link that opened a new tab: the job follows it
        old, s.page = s.page, popup
        asyncio.ensure_future(old.close())

    page.on("popup", adopt)
    _sessions[job_id] = s
    return s


async def _set_desktop(s: _Session, desktop: bool) -> None:
    """Give this one page a desktop identity and viewport (or take it back), through CDP."""
    if s.desktop == desktop:
        return
    try:
        cdp = await s.page.context.new_cdp_session(s.page)
        vp = DESKTOP_VIEWPORT if desktop else VIEWPORT
        scale = DEVICE_SCALE * VIEWPORT["width"] / vp["width"]  # the frame keeps its pixel size
        await cdp.send("Emulation.setUserAgentOverride", {"userAgent": DESKTOP_UA if desktop else MOBILE_UA, "acceptLanguage": "de-DE,de;q=0.9,en;q=0.8", "platform": "Win32" if desktop else "Linux armv8l"})
        await cdp.send("Emulation.setDeviceMetricsOverride", {"width": vp["width"], "height": vp["height"], "deviceScaleFactor": scale, "mobile": not desktop})
        await cdp.send("Emulation.setTouchEmulationEnabled", {"enabled": not desktop})
        s.desktop, s.viewport = desktop, dict(vp)
    except Exception:  # noqa: BLE001
        pass


async def page_for_job(job_id: str, *, lang: str = "de", desktop: Optional[bool] = None) -> Any:
    """The job's live Playwright page, for a deterministic wrapper (N-4's WhatsApp Web) that
    reads or drives the DOM itself. Hold `job_lock(job_id)` while you use it."""
    s = await _session(job_id, lang)
    if desktop is not None:
        await _set_desktop(s, desktop)
        s.desktop_forced = True
    return s.page


def job_lock(job_id: str) -> asyncio.Lock:
    s = _sessions.get(job_id)
    if s is None:
        raise KeyError(f"no session for job {job_id}; call page_for_job first")
    return s.lock


async def observe(job_id: str, *, lang: str = "de") -> dict[str, Any]:
    """The structured snapshot of the job's page: url, title, elements [{ref, tag, role, label,
    region, box [x, y, w, h], inView, picker, value, href, ...}], text, dialog, hasPassword,
    hasCode, captcha. Also refreshes the frame. Takes the job lock."""
    s = await _session(job_id, lang)
    async with s.lock:
        snap = await _observe(s)
    return dict(snap, frame_url=frame_url(job_id), viewport=dict(s.viewport))


async def close_job(job_id: str) -> None:
    s = _sessions.pop(job_id, None)
    if s is not None and not s.page.is_closed():
        try:
            await s.page.close()
        except Exception:  # noqa: BLE001
            pass


async def shutdown() -> None:
    global _pw, _context
    for job_id in list(_sessions):
        await close_job(job_id)
    if _context is not None:
        try:
            await _context.close()
        except Exception:  # noqa: BLE001
            pass
    if _pw is not None:
        try:
            await _pw.stop()
        except Exception:  # noqa: BLE001
            pass
    _context = _pw = None


def frame_url(job_id: str) -> str:
    s = _sessions.get(job_id)
    stamp = int((s.frame_at if s else time.time()) * 1000)
    return f"/jobs/{job_id}/frame.jpg?t={stamp}"


def latest_frame(job_id: str) -> Optional[bytes]:
    s = _sessions.get(job_id)
    return s.frame if s else None


# --------------------------------------------------------------------------- the page

async def _settle(page: Any, budget: float = SETTLE_BUDGET_S) -> None:
    """DOM-ready plus readiness, bounded: the document is loaded, the body has words, the
    network has gone quiet for a moment. Never a fixed sleep."""
    t0 = time.monotonic()
    left = lambda: max(0.2, budget - (time.monotonic() - t0))  # noqa: E731
    for state in ("domcontentloaded", "load"):
        try:
            await page.wait_for_load_state(state, timeout=int(left() * 1000))
        except Exception:  # noqa: BLE001
            break
    try:
        await page.wait_for_function("() => document.body && document.body.innerText.trim().length > 0", timeout=int(min(3.0, left()) * 1000))
    except Exception:  # noqa: BLE001
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=int(min(1.5, left()) * 1000))
    except Exception:  # noqa: BLE001
        pass


async def _snapshot(s: _Session) -> dict[str, Any]:
    try:
        snap = await s.page.evaluate(SNAPSHOT_JS, s.next_ref)
    except Exception as e:  # noqa: BLE001  the page is mid-navigation: try once more after it settles
        await _settle(s.page, 5.0)
        try:
            snap = await s.page.evaluate(SNAPSHOT_JS, s.next_ref)
        except Exception:  # noqa: BLE001
            snap = {"url": s.page.url, "title": "", "elements": [], "text": f"(the page could not be read: {type(e).__name__})", "ready": "loading"}
    s.next_ref = max(s.next_ref, int(snap.get("next") or 0))
    s.snap = snap
    return snap


def _downsample(frame: bytes) -> Optional[bytes]:
    """The model's copy of the frame: 390x844, small. Pillow when present, else None (the
    caller then takes a second, CSS-pixel screenshot)."""
    try:
        import io

        from PIL import Image

        im = Image.open(io.BytesIO(frame))
        im = im.convert("RGB").resize((VIEWPORT["width"], VIEWPORT["height"]), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=MODEL_QUALITY, optimize=True)
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return None


async def _screenshot(s: _Session) -> Optional[bytes]:
    try:
        s.frame = await s.page.screenshot(type="jpeg", quality=FRAME_QUALITY, scale="device", timeout=8000)
        s.frame_at = time.time()
        small = _downsample(s.frame)
        if small is None:
            small = await s.page.screenshot(type="jpeg", quality=MODEL_QUALITY, scale="css", timeout=8000)
        s.model_image = small
    except Exception:  # noqa: BLE001
        pass
    return s.frame


# WhatsApp Web's "Link with phone number" screen shows an 8-character code the member types into
# WhatsApp on their phone. It is rendered as a row of single-character cells (and sometimes carried
# on a [data-link-code] attribute). Read it, join, uppercase, group into fours (XXXX-XXXX) so the
# app can show it cleanly and copyably instead of the member reading it off the takeover frame. The
# code is never logged; the member's own phone number is never typed by the tool.
LINK_CODE_JS = r"""
() => {
  const clean = (t) => String(t || '').replace(/\s+/g, '').trim();
  const a = document.querySelector('[data-link-code]');
  if (a) { const v = clean(a.getAttribute('data-link-code')); if (/^[a-z0-9]{6,12}$/i.test(v)) return v; }
  let best = null;
  for (const el of document.querySelectorAll('div, section, ul, ol, [role=group], [role=list]')) {
    const kids = [...el.children].filter((c) => { const r = c.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
    if (kids.length < 6 || kids.length > 14) continue;
    const texts = kids.map((k) => clean(k.innerText || k.textContent));
    if (!texts.every((t) => /^[a-z0-9]$/i.test(t))) continue;  // every cell is exactly one alphanumeric char
    const joined = texts.join('');
    if (!best || joined.length > best.length) best = joined;
  }
  return best;
}
"""


def _is_whatsapp(url: str) -> bool:
    return (_host(url) or "").lower() == "web.whatsapp.com"


async def _whatsapp_link_code(page: Any) -> Optional[str]:
    """The link code on WhatsApp Web's phone-number screen, formatted XXXX-XXXX, or None. Never
    logged."""
    try:
        raw = await page.evaluate(LINK_CODE_JS)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    code = re.sub(r"[^A-Za-z0-9]", "", str(raw)).upper()
    if not (6 <= len(code) <= 12):
        return None
    return "-".join(code[i:i + 4] for i in range(0, len(code), 4))


def _code_hint(lang: str) -> str:
    """One line telling the member where to type the code. English by default; German when the
    member writes German."""
    if str(lang).lower().startswith("de"):
        return ("In WhatsApp: Einstellungen → Verknüpfte Geräte → Gerät hinzufügen → "
                "Stattdessen mit Telefonnummer verknüpfen, dann diesen Code eingeben.")
    return ("In WhatsApp: Settings > Linked Devices > Link a Device > Link with phone number "
            "instead, then enter this code.")


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").removeprefix("www.")
    except ValueError:
        return ""


def site_of(url: str) -> Optional[str]:
    host = (_host(url) or "").lower()
    for site in SITE_FACTS:
        if host == site or host.endswith("." + site):
            return site
    return None


def facts_line(url: str) -> str:
    site = site_of(url)
    if not site:
        return ""
    row = SITE_FACTS[site]
    line = f"Site facts ({site}): {row['facts']}"
    if row.get("cookie_reject"):
        line += f" Cookie banner: '{row['cookie_reject']}' (dismiss_dialog taps it)."
    return line


def site_facts_for_prompt() -> str:
    return "\n".join(f"- {site}: {row['facts']}" for site, row in SITE_FACTS.items())


def _clip(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _element_line(el: dict[str, Any]) -> str:
    kind = el.get("tag", "")
    if el.get("type") and kind == "input":
        kind += f"({el['type']})"
    if el.get("role"):
        kind += f"[{el['role']}]"
    line = f"{el['ref']} {kind} \"{_clip(str(el.get('label') or ''), 60)}\""
    if el.get("picker"):
        line += " [picker: use pick]"
    if el.get("file"):
        line += " [file input: use upload]"
    if el.get("href"):
        line += f" → {el['href']}"
    if el.get("value"):
        line += f" = \"{_clip(str(el['value']), 30)}\""
    if el.get("options"):
        line += " options: " + ", ".join(el["options"][:8])
    if el.get("checked"):
        line += " [checked]"
    if el.get("disabled"):
        line += " (disabled)"
    if not el.get("inView", True):
        line += " (off-screen)"
    return line


def _elements_text(elements: list[dict[str, Any]]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {r: [] for r in _REGIONS}
    for el in elements:
        groups.setdefault(el.get("region") or "main", []).append(el)
    # the budget goes to the dialog, the header, then what is on screen, then the rest
    main_in = [e for e in groups["main"] if e.get("inView", True)]
    main_off = [e for e in groups["main"] if not e.get("inView", True)]
    keep = groups["dialog"][:16] + groups["header"][:12]
    room = MAX_ELEMENTS - len(keep) - min(len(groups["footer"]), FOOTER_LINES)
    keep += (main_in + main_off)[: max(room, 0)]
    parts: list[str] = []
    for region in _REGIONS:
        rows = [e for e in keep if (e.get("region") or "main") == region] if region != "footer" else groups["footer"]
        if not rows:
            continue
        if region == "dialog":
            has_field = any(e.get("tag") in ("input", "textarea", "select") or e.get("picker") for e in rows)
            title = "[dialog — a panel covers the page; use its field, or dismiss_dialog]" if has_field else "[dialog — a banner or modal covers the page; dismiss_dialog closes it, or click one of these]"
            parts.append(title + "\n" + "\n".join(_element_line(e) for e in rows))
        elif region == "footer":
            more = len(rows) - FOOTER_LINES
            parts.append("[footer]\n" + "\n".join(_element_line(e) for e in rows[:FOOTER_LINES]) + (f"\n(+{more} more footer links)" if more > 0 else ""))
        else:
            hidden = len(groups[region]) - len(rows) if region == "main" else 0
            parts.append(f"[{region}]\n" + "\n".join(_element_line(e) for e in rows) + (f"\n(+{hidden} more further down; scroll to see them)" if hidden > 0 else ""))
    return "\n\n".join(parts)


def format_page(snap: dict[str, Any], *, note: str = "", full_text: str = "") -> str:
    url = str(snap.get("url") or "")
    parts = urlsplit(url) if url else None
    where = _host(url) + (parts.path if parts and parts.path not in ("", "/") else "") if url else ""
    head = [f"{_clip(str(snap.get('title') or ''), 100)} — {_clip(where, 100)}".strip(" —")]
    if note:
        head.append(note.strip())
    facts = facts_line(url)
    if facts:
        head.append(facts)
    if _JOIN_URL.search(parts.path if parts else ""):
        login = (SITE_FACTS.get(site_of(url) or "") or {}).get("sign_in")
        head.append("THIS IS A SIGN-UP PAGE, not the sign-in. Never fill it, never create an account."
                    + (f" The sign-in page is {login}; open it." if login else " Open the site's sign-in page instead."))
    if snap.get("dialog"):
        in_dialog = [e for e in snap.get("elements") or [] if e.get("region") == "dialog"]
        if any(e.get("tag") in ("input", "textarea", "select") or e.get("picker") for e in in_dialog):
            head.append(f"A panel is open: \"{_clip(str(snap['dialog']), 90)}\" with a field in it. Use its field if that is the way forward, else dismiss_dialog.")
        else:
            head.append(f"A dialog is open: \"{_clip(str(snap['dialog']), 90)}\". Clear it first (dismiss_dialog prefers reject or close).")
    if snap.get("captcha"):
        head.append("A human check (captcha) is on the page: use needs_you.")
    elif snap.get("challenge"):
        head.append("A bot check is running (\"Nur einen Moment…\"); it usually passes by itself: wait_for the page's words for 10 s, and if it asks you to tick or tap, use needs_you.")
    elif snap.get("hasCode"):
        head.append("The page asks for a one-time code: use needs_you.")
    elif snap.get("hasPassword"):
        head.append("The page asks for a password: never type one; use needs_you and say so in one sentence.")
    elif _LOGIN_WALL.search(f"{snap.get('dialog') or ''} {snap.get('title') or ''} {str(snap.get('text') or '')[:200]}"):
        head.append("The page asks for a sign-in before it shows more. Use needs_you; the member signs in on their phone and you continue.")
    if snap.get("ready") not in (None, "complete", "interactive"):
        head.append("(the page is still loading; this is what it shows now)")
    sy, sh, vh = int(snap.get("scrollY") or 0), int(snap.get("scrollH") or 0), int(snap.get("viewH") or 1)
    if sh > vh + 40:
        head.append(f"(showing {sy}–{min(sy + vh, sh)} of {sh}px; scroll for more)")
    out = "\n".join(head)
    elements = _elements_text(list(snap.get("elements") or []))
    if elements:
        out += "\n\n" + elements
    if full_text:
        out += "\n\n[page text]\n" + _clip(full_text, READ_LIMIT)
    else:
        out += "\n\n[text]\n" + _clip(str(snap.get("text") or ""), TEXT_EXCERPT) + ("\n(read gives the whole text)" if len(str(snap.get("text") or "")) > TEXT_EXCERPT else "")
    return out


# --------------------------------------------------------------------------- step sentences

def _step(lang: str, action: str, ok: bool, **f: Any) -> str:
    de = lang.lower().startswith("de")
    host = f.get("host") or "der Seite" if de else f.get("host") or "the page"
    label = _clip(str(f.get("label") or ""), 40)
    if not ok:
        why = {"click": f"„{label}“ ließ sich nicht antippen." if de else f"Could not tap “{label}”.",
               "goto": f"{host} hat nicht geladen." if de else f"{host} did not load.",
               "pick": f"„{label}“ ließ sich nicht auswählen." if de else f"Could not pick “{label}”.",
               "wait_for": f"„{label}“ ist nicht erschienen." if de else f"“{label}” did not appear.",
               "dismiss_dialog": "Der Hinweis ließ sich nicht schließen." if de else "The banner would not close."}
        return why.get(action, "Das hat nicht geklappt, ich versuche es anders." if de else "That did not work, trying another way.")
    lines = {
        "goto": f"{host} geöffnet." if de else f"Opened {host}.",
        "click": f"„{label}“ angetippt." if de else f"Tapped “{label}”.",
        "fill": (f"{f.get('n', 1)} Felder ausgefüllt." if f.get("n", 1) != 1 else "Ein Feld ausgefüllt.") if de else (f"Filled {f.get('n', 1)} fields." if f.get("n", 1) != 1 else "Filled one field."),
        "pick": f"„{label}“ ausgewählt." if de else f"Picked “{label}”.",
        "press": f"{label} gedrückt." if de else f"Pressed {label}.",
        "scroll": "Weiter gescrollt." if de else "Scrolled on.",
        "read": "Seite gelesen." if de else "Read the page.",
        "wait_for": f"„{label}“ ist da." if de else f"“{label}” is there.",
        "dismiss_dialog": f"Hinweis geschlossen („{label}“)." if de else f"Closed the banner (“{label}”).",
        "upload": "Datei hochgeladen." if de else "Uploaded the file.",
        "needs_you": (f"Hier brauche ich dich: {f.get('reason') or 'bitte übernimm kurz.'}" if de else f"I need you here: {f.get('reason') or 'please take over for a moment.'}"),
        "resume": "Weiter geht's." if de else "Continuing.",
    }
    return lines.get(action, "Weiter." if de else "On it.")


# --------------------------------------------------------------------------- actions

async def _ref_box(s: _Session, ref: str) -> Optional[dict[str, float]]:
    try:
        return await s.page.evaluate(REF_BOX_JS, ref)
    except Exception:  # noqa: BLE001
        return None


def _label_of(s: _Session, ref: str) -> str:
    for el in s.snap.get("elements") or []:
        if el.get("ref") == ref:
            return str(el.get("label") or ref)
    return ref


async def _handle(s: _Session, ref: str) -> Any:
    """The element behind a ref, or None when the page moved on."""
    try:
        h = await s.page.evaluate_handle("(r) => (window.__ola || {refs: {}}).refs[r] || null", ref)
        el = h.as_element()
        return el
    except Exception:  # noqa: BLE001
        return None


CHALLENGE_JS = "() => /just a moment|nur einen moment|sicherheitsüberprüfung|checking your browser|verify you are human|attention required/i.test(document.title + ' ' + ((document.body && document.body.innerText) || '').slice(0, 500))"


async def _pass_challenge(s: _Session, budget: float = 12.0) -> None:
    """A bot-check interstitial usually clears by itself within seconds: give it that long, bounded."""
    try:
        if not await s.page.evaluate(CHALLENGE_JS):
            return
        await s.page.wait_for_function("!(" + CHALLENGE_JS + ")()", timeout=int(budget * 1000))
        await _settle(s.page, 5.0)
    except Exception:  # noqa: BLE001
        pass  # still on the check: the snapshot says so


async def _do_goto(s: _Session, url: str) -> tuple[bool, str]:
    if not url:
        return False, "goto needs a url"
    if not re.match(r"^[a-z]+://", url, re.I):
        url = "https://" + url
    if not s.desktop_forced:
        await _set_desktop(s, (urlsplit(url).hostname or "").lower() in DESKTOP_SITES)
    try:
        await s.page.goto(url, wait_until="domcontentloaded", timeout=int(ACTION_BUDGET_S * 1000))
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0] if str(e) else type(e).__name__
        if "Timeout" in type(e).__name__ or "Timeout" in msg:
            return False, f"{_host(url)} did not finish loading within {ACTION_BUDGET_S:.0f} s; this is what it shows now"
        return False, f"could not open {url}: {_clip(msg, 120)}"
    await _settle(s.page)
    await _pass_challenge(s)
    hint = READY_HINTS.get((urlsplit(url).hostname or "").lower().removeprefix("www."))
    if hint:
        try:
            await s.page.wait_for_selector(hint, state="visible", timeout=15000)
            await _settle(s.page, 3.0)
        except Exception:  # noqa: BLE001
            pass  # the page as it is now; the model can wait_for
    return True, ""


async def _do_click(s: _Session, ref: str) -> tuple[bool, str]:
    if not ref:
        return False, "click needs a ref like e12"
    el = await _handle(s, ref)
    if el is None:
        return False, f"{ref} is not on the page any more; the refs below are fresh, choose again"
    try:
        await el.scroll_into_view_if_needed(timeout=3000)
        await el.click(timeout=8000)
    except Exception as e:  # noqa: BLE001
        try:  # something covers it or it moved: tap where it is
            box = await _ref_box(s, ref)
            if box and box["w"] > 0:
                await s.page.mouse.click(box["x"], box["y"])
            else:
                raise e
        except Exception:  # noqa: BLE001
            return False, f"could not tap {ref} \"{_label_of(s, ref)}\" ({type(e).__name__}: something may cover it, a dialog?); the refs below are fresh"
    await _settle(s.page, 5.0)
    await _pass_challenge(s)
    return True, ""


async def _do_fill(s: _Session, fields: Any) -> tuple[bool, str, int]:
    if not isinstance(fields, list) or not fields:
        return False, "fill needs fields: [{ref, text}, ...]", 0
    done, problems = 0, []
    for f in fields:
        ref, text = str((f or {}).get("ref") or ""), str((f or {}).get("text") or "")
        el = await _handle(s, ref)
        if el is None:
            problems.append(f"{ref} is not on the page any more")
            continue
        try:
            await el.scroll_into_view_if_needed(timeout=3000)
            await el.fill(text, timeout=8000)
            done += 1
        except Exception:  # noqa: BLE001
            try:
                await el.click(timeout=3000)
                await s.page.keyboard.press("Control+A")
                await s.page.keyboard.type(text)
                done += 1
            except Exception as e2:  # noqa: BLE001
                problems.append(f"{ref} \"{_label_of(s, ref)}\" could not be filled ({type(e2).__name__})")
    await _settle(s.page, 3.0)
    if problems:
        return False, "; ".join(problems) + "; the refs below are fresh", done
    return True, "", done


async def _field_shows(s: _Session, ref: str) -> str:
    try:
        return str(await s.page.evaluate(FIELD_SHOWS_JS, ref) or "")
    except Exception:  # noqa: BLE001
        return ""


async def _do_pick(s: _Session, ref: str, text: str) -> tuple[bool, str, str]:
    """Type, wait for the list, tap the matching row, check the field shows it."""
    if not ref or not text:
        return False, "pick needs ref and text", ""
    el = await _handle(s, ref)
    if el is None:
        return False, f"{ref} is not on the page any more; the refs below are fresh", ""
    try:
        await el.scroll_into_view_if_needed(timeout=3000)
        await el.click(timeout=5000)
        await el.fill("", timeout=3000)
        head, tail = (text[:-2], text[-2:]) if len(text) > 4 else ("", text)
        if head:
            await el.fill(head, timeout=3000)
        await el.type(tail, delay=90)  # widgets that listen per keystroke open on these
    except Exception as e:  # noqa: BLE001
        return False, f"could not type into {ref} ({type(e).__name__}); the refs below are fresh", ""
    seen: Optional[dict[str, Any]] = None
    deadline = time.monotonic() + PICK_WAIT_S
    while time.monotonic() < deadline:
        try:
            res = await s.page.evaluate(SUGGESTIONS_JS, text)
        except Exception:  # noqa: BLE001
            res = None
        if isinstance(res, dict) and res.get("open"):
            seen = res
            if res.get("matched"):
                break
        await asyncio.sleep(0.3)
    shows = await _field_shows(s, ref)
    if seen is None:
        return False, f"no suggestion list opened after typing '{_clip(text, 60)}'; the field shows '{_clip(shows, 60)}'. If it is a plain field, use fill.", shows
    if not seen.get("matched"):
        return False, f"the list opened but no row matches '{_clip(text, 60)}'; it offers: " + " | ".join(seen.get("rows") or []), shows
    await s.page.mouse.click(float(seen["x"]), float(seen["y"]))
    picked = str(seen.get("text") or "")
    typed = " ".join(text.lower().split())
    settle_until = time.monotonic() + 3.0
    while True:  # the site fills the field a beat after the tap (a geocode round-trip)
        shows = await _field_shows(s, ref)
        taken = bool(shows) and " ".join(shows.lower().split()) != typed
        try:
            again = await s.page.evaluate(SUGGESTIONS_JS, text)
            still_open = bool(isinstance(again, dict) and again.get("open") and again.get("text") == picked)
        except Exception:  # noqa: BLE001
            still_open = False
        if taken or (shows and not still_open) or time.monotonic() >= settle_until:
            break
        await asyncio.sleep(0.3)
    await _settle(s.page, 4.0)
    if not shows and s.page.url != s.snap.get("url"):
        return True, "", picked  # the pick moved the page on (a search that ran on the pick)
    ok = bool(shows) and (" ".join(shows.lower().split()) != typed or not still_open)
    if not ok:
        return False, (f"tapped the row '{_clip(picked, 60)}' but the field shows nothing" if not shows else f"tapped '{_clip(picked, 60)}' but the field still shows only the typed text and the list is still open"), shows
    return True, "", picked


async def _do_press(s: _Session, key: str) -> tuple[bool, str]:
    if not key:
        return False, "press needs a key"
    try:
        await s.page.keyboard.press(key)
    except Exception as e:  # noqa: BLE001
        return False, f"could not press {key}: {type(e).__name__}"
    await _settle(s.page, 5.0)
    return True, ""


async def _do_scroll(s: _Session, dy: Any) -> tuple[bool, str]:
    try:
        d = int(dy) if dy not in (None, "") else 600
    except (TypeError, ValueError):
        d = 600
    await s.page.mouse.move(s.viewport["width"] / 2, s.viewport["height"] / 2)
    await s.page.mouse.wheel(0, d)
    await _settle(s.page, 2.0)
    return True, ""


async def _do_wait_for(s: _Session, text: str, url: str, seconds: Any) -> tuple[bool, str]:
    kind, want = ("url", url) if url else ("text", text)
    if not want:
        return False, "wait_for needs text or url"
    try:
        budget = min(max(float(seconds or 10), 1.0), ACTION_BUDGET_S)
    except (TypeError, ValueError):
        budget = 10.0
    t0 = time.monotonic()
    while time.monotonic() - t0 < budget:
        try:
            if await s.page.evaluate(WAIT_JS, [kind, want]):
                await _settle(s.page, 2.0)
                return True, ""
        except Exception:  # noqa: BLE001
            pass  # the page is navigating; look again
        await asyncio.sleep(0.3)
    return False, f"'{_clip(want, 60)}' did not appear within {budget:.0f} s; this is what the page shows now"


async def _do_dismiss(s: _Session) -> tuple[bool, str, str]:
    try:
        res = await s.page.evaluate(DISMISS_JS)
    except Exception as e:  # noqa: BLE001
        return False, f"could not look for a dialog: {type(e).__name__}", ""
    if not res.get("found"):
        return False, "no banner or dialog is open", ""
    if not res.get("kind"):
        return False, "a dialog is open but it has no reject, close or accept control I recognise; its buttons: " + ", ".join(res.get("buttons") or []), ""
    await s.page.mouse.click(float(res["x"]), float(res["y"]))
    t0 = time.monotonic()
    while time.monotonic() - t0 < 2.5:
        try:
            if not await s.page.evaluate(DIALOG_OPEN_JS):
                break
        except Exception:  # noqa: BLE001
            break
        await asyncio.sleep(0.2)
    await _settle(s.page, 3.0)
    clicked = str(res.get("clicked") or res.get("kind"))
    return True, ("" if res.get("kind") != "accept" else "the banner offered no reject or close control, so accept was tapped"), clicked


async def _do_upload(s: _Session, ref: str, attachment_id: str, resolver: Optional[Callable[[str], Any]]) -> tuple[bool, str]:
    if not ref or not attachment_id:
        return False, "upload needs ref (a file input) and attachment_id"
    path = None
    if resolver is not None:
        try:
            path = resolver(attachment_id)
            if asyncio.iscoroutine(path):
                path = await path
        except Exception:  # noqa: BLE001
            path = None
    if not path or not Path(path).exists():
        return False, f"attachment {attachment_id} is not on disk"
    el = await _handle(s, ref)
    if el is None:
        return False, f"{ref} is not on the page any more; the refs below are fresh"
    try:
        await el.set_input_files(str(path), timeout=8000)
    except Exception as e:  # noqa: BLE001
        return False, f"{ref} took no file ({type(e).__name__}); is it a file input?"
    await _settle(s.page, 3.0)
    return True, ""


# --------------------------------------------------------------------------- the entry points

async def run(job_id: str, args: dict[str, Any], *, lang: str = "de", attachment_path: Optional[Callable[[str], Any]] = None) -> BrowserResult:
    """One tool call of one job. Never raises for a page problem: the result says what happened."""
    args = dict(args or {})
    action = str(args.get("action") or "").strip()
    s = await _session(job_id, lang)
    async with s.lock:
        try:
            return await asyncio.wait_for(_run(s, action, args, attachment_path), timeout=ACTION_BUDGET_S + 15)
        except asyncio.TimeoutError:
            snap = await _observe(s)
            return BrowserResult(format_page(snap, note=f"{action} took longer than {ACTION_BUDGET_S:.0f} s; this is what the page shows now"), s.model_image, _step(s.lang, action, False), ok=False, url=str(snap.get("url") or ""))


async def _observe(s: _Session) -> dict[str, Any]:
    snap = await _snapshot(s)
    await _screenshot(s)
    return snap


async def _run(s: _Session, action: str, args: dict[str, Any], resolver: Optional[Callable[[str], Any]]) -> BrowserResult:
    lang = s.lang
    if action not in ACTIONS:
        snap = await _observe(s)
        return BrowserResult(format_page(snap, note=f"unknown action '{action}'; use one of {', '.join(ACTIONS)}"), s.model_image, _step(lang, action, False), ok=False, url=snap.get("url", ""))
    ok, note, full_text, step_fields = True, "", "", {}
    if action == "goto":
        ok, note = await _do_goto(s, str(args.get("url") or ""))
        step_fields = {"host": _host(s.page.url) or _host(str(args.get("url") or ""))}
    elif action == "click":
        ref = str(args.get("ref") or "")
        step_fields = {"label": _label_of(s, ref)}
        ok, note = await _do_click(s, ref)
    elif action == "fill":
        ok, note, n = await _do_fill(s, args.get("fields"))
        step_fields = {"n": n}
    elif action == "pick":
        ok, note, picked = await _do_pick(s, str(args.get("ref") or ""), str(args.get("text") or ""))
        step_fields = {"label": picked or str(args.get("text") or "")}
        if ok:
            note = f"picked '{_clip(picked, 80)}' for {args.get('ref')}"
    elif action == "press":
        key = str(args.get("key") or "")
        ok, note = await _do_press(s, key)
        step_fields = {"label": key}
    elif action == "scroll":
        ok, note = await _do_scroll(s, args.get("dy"))
    elif action == "read":
        try:
            full_text = str(await s.page.evaluate(FULL_TEXT_JS))
        except Exception:  # noqa: BLE001
            ok, note = False, "the page could not be read right now; this is what it shows"
    elif action == "wait_for":
        ok, note = await _do_wait_for(s, str(args.get("text") or ""), str(args.get("url") or ""), args.get("seconds"))
        step_fields = {"label": str(args.get("url") or args.get("text") or "")}
    elif action == "dismiss_dialog":
        ok, note, clicked = await _do_dismiss(s)
        step_fields = {"label": clicked}
    elif action == "upload":
        ok, note = await _do_upload(s, str(args.get("ref") or ""), str(args.get("attachment_id") or ""), resolver)
    elif action == "needs_you":
        reason = str(args.get("reason") or "").strip()
        snap = await _observe(s)
        url = str(snap.get("url") or s.page.url)
        need: dict[str, str] = {"reason": reason or ("Bitte übernimm kurz." if lang.startswith("de") else "Please take over for a moment."), "url": url}
        code = await _whatsapp_link_code(s.page) if _is_whatsapp(url) else None
        hint = None
        if code:
            hint = _code_hint(lang)
            need["code"], need["code_hint"] = code, hint
        text = format_page(snap, note="The member is on this page now. You get the page again when they are done; then continue.")
        return BrowserResult(text, s.model_image, _step(lang, "needs_you", True, reason=reason), ok=True, needs_you=need, url=url, code=code, code_hint=hint)
    snap = await _observe(s)
    if not ok:
        note = f"ACTION FAILED: {note}"
    code = await _whatsapp_link_code(s.page) if _is_whatsapp(str(snap.get("url") or "")) else None
    return BrowserResult(format_page(snap, note=note, full_text=full_text), s.model_image, _step(lang, action, ok, **step_fields), ok=ok, url=str(snap.get("url") or ""), code=code)


async def after_resume(job_id: str) -> BrowserResult:
    """The page after the member handed it back: the tool result of the needs_you call."""
    s = await _session(job_id)
    async with s.lock:
        await _settle(s.page, 5.0)
        snap = await _observe(s)
        return BrowserResult(format_page(snap, note="The member has handed the page back to you. This is what it shows now."), s.model_image, _step(s.lang, "resume", True), ok=True, url=str(snap.get("url") or ""))


async def member_input(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """A tap, typing, a key or a scroll from the member's takeover view (viewport coordinates)."""
    s = _sessions.get(job_id)
    if s is None or s.page.is_closed():
        return {"ok": False, "error": "no page for this job"}
    kind = str(body.get("kind") or "")
    k = s.viewport["width"] / VIEWPORT["width"]  # the phone taps in 390x844; a desktop page is twice that
    async with s.lock:
        try:
            if kind == "tap":
                await s.page.mouse.click(float(body.get("x") or 0) * k, float(body.get("y") or 0) * k)
            elif kind == "type":
                await s.page.keyboard.type(str(body.get("text") or ""))
            elif kind == "key":
                await s.page.keyboard.press(str(body.get("key") or "Enter"))
            elif kind == "scroll":
                await s.page.mouse.move(s.viewport["width"] / 2, s.viewport["height"] / 2)
                await s.page.mouse.wheel(0, float(body.get("dy") or 400) * k)
            else:
                return {"ok": False, "error": "kind must be tap, type, key or scroll"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": type(e).__name__}
        await _settle(s.page, 2.0)
        await _screenshot(s)
    return {"ok": True, "url": s.page.url, "frame_url": frame_url(job_id), "viewport": dict(s.viewport)}


async def fresh_frame(job_id: str, max_age: float = 0.3) -> Optional[bytes]:
    s = _sessions.get(job_id)
    if s is None:
        return None
    if s.page.is_closed() or (time.time() - s.frame_at < max_age):
        return s.frame
    if s.lock.locked():
        return s.frame  # an action is running; its own screenshot follows
    async with s.lock:
        await _screenshot(s)
    return s.frame


def mount(app: Any, auth: Optional[Callable[..., Any]] = None) -> None:
    """The two takeover routes on N-1's FastAPI app: the latest frame, and the member's input."""
    deps = [Depends(auth)] if auth is not None else []

    @app.get("/jobs/{job_id}/frame.jpg", dependencies=deps)
    async def _frame(job_id: str) -> Response:
        """The latest frame, re-taken when older than a third of a second: polling at 2 fps during
        a takeover always shows the page as it is. 1170x2532 device pixels; taps are sent in 390x844."""
        data = await fresh_frame(job_id)
        if data is None:
            raise HTTPException(status_code=404, detail="no frame for this job")
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Ola-Viewport": f"{VIEWPORT['width']}x{VIEWPORT['height']}"})

    @app.post("/jobs/{job_id}/input", dependencies=deps)
    async def _input(job_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        out = await member_input(job_id, body if isinstance(body, dict) else {})
        if not out.get("ok"):
            raise HTTPException(status_code=404 if out.get("error") == "no page for this job" else 400, detail=out.get("error") or "input failed")
        return out


__all__ = [
    "TOOL", "ACTIONS", "SITE_FACTS", "DESKTOP_SITES", "BrowserResult", "run", "after_resume", "member_input", "fresh_frame",
    "observe", "page_for_job", "job_lock",
    "latest_frame", "frame_url", "close_job", "shutdown", "mount", "format_page", "facts_line", "site_of",
    "site_facts_for_prompt",
]
