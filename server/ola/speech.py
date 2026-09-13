"""Ola's spoken voice: OpenAI text-to-speech, mp3 bytes for the app. Default tts-1-hd (the clean
classic OpenAI voices); gpt-4o-mini-tts also works and then gets the voice instructions."""

from __future__ import annotations

import os
import re

import httpx

TTS_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_MODEL = "tts-1-hd"
DEFAULT_VOICE = "nova"
INSTRUCTABLE = ("gpt-4o-mini-tts",)  # only these models accept an instructions field
MAX_CHARS = 1500
LANGUAGE_NAMES = {"de": "German", "en": "English"}


class SpeechUnavailable(RuntimeError):
    """No key, or OpenAI did not answer with audio; the app falls back to the system voice."""


def strip_markdown(text: str) -> str:
    """Plain words for the voice: no emphasis marks, list bullets, headings, links or code ticks."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [label](url) -> label
    text = re.sub(r"<https?://[^>]+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+•]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text, flags=re.S)
    text = re.sub(r"(?<!\w)[*_](.+?)[*_](?!\w)", r"\1", text, flags=re.S)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def instructions(language: str | None) -> str:
    name = LANGUAGE_NAMES.get((language or "").lower()[:2], language or "the member's language")
    return f"Speak warm, natural and friendly, like a close friend talking, in {name}. Plain speech, no reading of symbols."


class Speech:
    def __init__(
        self,
        api_key: str | None = None,
        voice: str | None = None,
        client: httpx.AsyncClient | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.voice = voice or os.environ.get("OLA_TTS_VOICE") or DEFAULT_VOICE
        self.model = model or os.environ.get("OLA_TTS_MODEL") or DEFAULT_TTS_MODEL
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def speak(self, text: str, language: str | None = None) -> bytes:
        if not self.api_key:
            raise SpeechUnavailable("no OPENAI_API_KEY")
        plain = strip_markdown(text)[:MAX_CHARS].strip()
        if not plain:
            raise SpeechUnavailable("nothing to say")
        body = {"model": self.model, "voice": self.voice, "input": plain, "response_format": "mp3"}
        if self.model in INSTRUCTABLE:
            body["instructions"] = instructions(language)
        try:
            r = await self._client.post(TTS_URL, json=body, headers={"Authorization": f"Bearer {self.api_key}"})
        except httpx.HTTPError as e:
            raise SpeechUnavailable(f"OpenAI unreachable: {type(e).__name__}") from e
        if r.status_code != 200:
            raise SpeechUnavailable(f"OpenAI answered {r.status_code}")
        if not r.content:
            raise SpeechUnavailable("OpenAI sent no audio")
        return r.content
