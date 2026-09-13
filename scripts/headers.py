# Copyright © 2026 Jayden Robert Bruck. All rights reserved.
"""Apply the Ola header to tracked Python and Swift sources, or check with --check."""

import argparse
from pathlib import Path
import subprocess

HEADER = "Copyright © 2026 Jayden Robert Bruck. All rights reserved."
ROOT = Path(__file__).resolve().parents[1]


def with_header(data: bytes, suffix: str) -> bytes:
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    body = data[len(bom):]
    newline = b"\r\n" if b"\r\n" in body else b"\n"
    line = (("# " if suffix == ".py" else "// ") + HEADER).encode("utf-8")
    lines = body.splitlines(keepends=True)
    index = 1 if lines and lines[0].startswith(b"#!") else 0
    # Keep Python's optional encoding declaration on its original first/second line.
    if suffix == ".py":
        import re
        for i in range(min(2, len(lines))):
            if re.match(br"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+", lines[i]):
                index = max(index, i + 1)
    if index < len(lines) and lines[index].rstrip(b"\r\n") == line:
        return data
    prefix = b"".join(lines[:index])
    if prefix and not prefix.endswith(b"\n"):
        prefix += newline
    return bom + prefix + line + newline + b"".join(lines[index:])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report missing headers without writing")
    args = parser.parse_args()
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "*.py", "*.swift"], cwd=ROOT
    ).decode("utf-8").split("\0")
    changed = []
    for name in filter(None, names):
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Tracked source is not a regular file: {name}")
        before = path.read_bytes()
        after = with_header(before, path.suffix)
        if before != after:
            changed.append(name)
            if not args.check:
                path.write_bytes(after)
    for name in changed:
        print(("Missing: " if args.check else "Updated: ") + name)
    print(f"{len(changed)} source files {"missing headers" if args.check else "updated"}.")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
