#!/usr/bin/env python3
"""Teach Ola a fact from the command line: python scripts/remember.py "Home: Dieburger Straße 48a, 63303 Dreieich." """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from ola.memory import Memory  # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: remember.py <fact> [<fact> ...]")
        sys.exit(2)
    mem = Memory()
    for fact in sys.argv[1:]:
        print(mem.remember(fact))
    print(f"{len(mem.facts())} facts in {mem.path}")
