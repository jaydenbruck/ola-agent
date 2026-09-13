"""POST /speak with a stubbed OpenAI: mp3 back, 503 without a key or on failure, markdown stripped."""

from __future__ import annotations

import json

import httpx
import pytest

from ola.main import create_app
from ola.speech import MAX_CHARS, Speech, SpeechUnavailable, instructions, strip_markdown

H = {"Authorization": "Bearer s"}


def test_strip_markdown():
    text = "**Deine Pizza** ist da: *9,90 €*.\n\n- Margherita\n1. Uber: [14 €](https://u.be/x)\n## Noch was\n`code` und ```x = 1``` fertig <https://a.b>"
    assert strip_markdown(text) == "Deine Pizza ist da: 9,90 €.\nMargherita\nUber: 14 €\nNoch was\ncode und fertig"
    assert strip_markdown("Sieh https://example.com/a?b=1 an.") == "Sieh an."
    assert strip_markdown("Preis: 2*3 = 6 und snake_case bleibt") == "Preis: 2*3 = 6 und snake_case bleibt"
    assert instructions("de").endswith("in German. Plain speech, no reading of symbols.")
    assert "English" in instructions("en-US") and "the member's language" in instructions(None)


def stub(status: int = 200, content: bytes = b"ID3mp3", seen: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content) | {"auth": request.headers.get("Authorization")})
        return httpx.Response(status, content=content)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_speak_sends_plain_text_to_openai_and_returns_mp3():
    seen: list = []
    sp = Speech(api_key="k", voice="nova", client=stub(seen=seen))
    audio = await sp.speak("**Hallo** Jayden, " + "x" * 2000, "de")
    assert audio == b"ID3mp3"
    body = seen[0]
    assert body["model"] == "tts-1-hd" and body["voice"] == "nova" and body["response_format"] == "mp3"
    assert body["input"].startswith("Hallo Jayden, x") and len(body["input"]) == MAX_CHARS
    assert "instructions" not in body, "the classic voices take no instructions field"
    assert body["auth"] == "Bearer k"
    sp = Speech(api_key="k", voice="nova", client=stub(seen=seen), model="gpt-4o-mini-tts")
    await sp.speak("Hallo", "de")
    assert seen[1]["model"] == "gpt-4o-mini-tts" and "German" in seen[1]["instructions"]


async def test_speak_failures_are_plain():
    with pytest.raises(SpeechUnavailable, match="no OPENAI_API_KEY"):
        await Speech(api_key="", client=stub()).speak("hi")
    with pytest.raises(SpeechUnavailable, match="OpenAI answered 429"):
        await Speech(api_key="k", client=stub(429, b"slow")).speak("hi")
    with pytest.raises(SpeechUnavailable, match="nothing to say"):
        await Speech(api_key="k", client=stub()).speak("**  **")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(SpeechUnavailable, match="unreachable"):
        await Speech(api_key="k", client=httpx.AsyncClient(transport=httpx.MockTransport(boom))).speak("hi")


async def test_speak_route(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = create_app(token="s", load_optional_tools=False, speech=Speech(api_key="k", client=stub()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/speak", json={"text": "Hallo"})).status_code == 401
        r = await c.post("/speak", json={"text": "Hallo, **Jayden**!", "language": "de"}, headers=H)
        assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and r.content == b"ID3mp3"
        assert (await c.post("/speak", json={"text": ""}, headers=H)).status_code == 422
    app = create_app(token="s", load_optional_tools=False, speech=Speech(api_key="", client=stub()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/speak", json={"text": "Hallo"}, headers=H)
        assert r.status_code == 503 and r.json() == {"reason": "no OPENAI_API_KEY"}
    app = create_app(token="s", load_optional_tools=False, speech=Speech(api_key="k", client=stub(500, b"x")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post("/speak", json={"text": "Hallo"}, headers=H)
        assert r.status_code == 503 and r.json() == {"reason": "OpenAI answered 500"}
