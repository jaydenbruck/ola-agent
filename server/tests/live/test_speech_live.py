"""Ola's real voice: one short German sentence through OpenAI text-to-speech. Skipped without OPENAI_API_KEY."""

from __future__ import annotations

import os

import pytest

from ola.main import load_env
from ola.speech import Speech

load_env()
pytestmark = pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OPENAI_API_KEY: live text-to-speech skipped")


async def test_german_sentence_becomes_mp3(tmp_path):
    sp = Speech()
    audio = await sp.speak("Hallo Jayden, deine Pizza ist unterwegs und kommt in etwa **zwanzig Minuten**.", "de")
    await sp.aclose()
    assert len(audio) > 5000, "an mp3 of a short sentence is several kilobytes"
    assert audio[:3] == b"ID3" or audio[0] == 0xFF, "mp3 header"
    (tmp_path / "ola-voice.mp3").write_bytes(audio)
