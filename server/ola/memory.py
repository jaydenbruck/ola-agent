"""What Ola knows about the member: plain facts in facts.json, read at every turn, written by `remember`."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "memory" / "facts.json"


class Memory:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or os.environ.get("OLA_FACTS") or DEFAULT_PATH)

    def facts(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        facts = data.get("facts", []) if isinstance(data, dict) else data
        return [str(f).strip() for f in facts if str(f).strip()]

    def remember(self, fact: str) -> str:
        fact = " ".join(str(fact).split())
        if not fact:
            return "Nothing to remember."
        facts = self.facts()
        if fact in facts:
            return "Already known."
        facts.append(fact)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"facts": facts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return "Remembered."

    def forget(self, fact: str) -> str:
        facts = [f for f in self.facts() if fact.strip() not in f]
        self.path.write_text(json.dumps({"facts": facts}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return "Forgotten."

    def language(self) -> str | None:
        """'en' or 'de' when a fact names the member's language; the one named first wins."""
        text = " ".join(self.facts()).lower()
        first = {lang: min((text.find(w) for w in words if w in text), default=-1)
                 for lang, words in (("en", ("englisch", "english")), ("de", ("deutsch", "german")))}
        named = {lang: pos for lang, pos in first.items() if pos >= 0}
        return min(named, key=named.get) if named else None

    def prompt_block(self) -> str:
        facts = self.facts()
        if not facts:
            return "You know nothing about the member yet. Whatever they tell you about themselves, remember it."
        return "What you know about the member:\n" + "\n".join(f"- {f}" for f in facts)
