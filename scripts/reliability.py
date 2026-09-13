"""Run every Python suite and publish measured evidence, including honest skips."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "build" / "reliability"
BERLIN = ZoneInfo("Europe/Berlin")


def stamp():
    return datetime.now(BERLIN).isoformat(timespec="seconds")


def environment(paths):
    env = os.environ.copy()
    for path in paths:
        if path.exists():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                match = re.match(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*)$", line.strip())
                if match:
                    env.setdefault(match[1], match[2].strip().strip("\"'"))
    env["PYTHONPATH"] = str(ROOT / "server") + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def redact(text, env):
    values = [v for k, v in env.items() if v and len(v) >= 4 and
              any(part in k for part in ("KEY", "TOKEN", "PASSWORD", "SECRET", "OLA_WHATSAPP_TEST_CONTACT", "CLIENT_ID"))]
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return re.sub(r"Bearer\s+[\w.\-]+", "Bearer [redacted]", text, flags=re.I)


def read_cases(path, suite):
    cases = []
    for item in ET.parse(path).iter("testcase"):
        state, detail = "passed", ""
        for tag, label in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            node = item.find(tag)
            if node is not None:
                state, detail = label, node.get("message", "")
                break
        cases.append({"suite": suite, "name": item.get("name", "unnamed"),
                      "class": item.get("classname", ""), "state": state, "detail": detail,
                      "seconds": float(item.get("time", "0"))})
    return cases


def run_suite(suite, env, timeout):
    path = ROOT / "server" / "tests" / suite
    xml_path = OUT / (suite + ".xml")
    xml_path.unlink(missing_ok=True)
    command = [sys.executable, "-m", "pytest", str(path), "-q", "--tb=short", "--junitxml=" + str(xml_path)]
    started = stamp()
    try:
        run = subprocess.run(command, cwd=ROOT / "server", env=env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, output = run.returncode, run.stdout + run.stderr
    except subprocess.TimeoutExpired:
        code, output = 124, "Suite exceeded its execution deadline. Results are incomplete."
    (OUT / (suite + ".log")).write_text(redact(output, env), encoding="utf-8")
    cases = []
    if xml_path.exists():
        xml_path.write_text(redact(xml_path.read_text(encoding="utf-8"), env), encoding="utf-8")
        try:
            cases = read_cases(xml_path, suite)
        except ET.ParseError:
            code = 125
    if code not in (0, 1) or not cases:
        cases.append({"suite": suite, "class": "collection", "name": "Suite completion", "state": "error",
                      "detail": f"pytest exit {code}; see build/reliability/{suite}.log", "seconds": 0})
    return {"suite": suite, "command": f"python -m pytest server/tests/{suite} -q --tb=short --junitxml=build/reliability/{suite}.xml",
            "started": started, "ended": stamp(), "exit": code, "cases": cases}


def capability(case):
    name = (case["class"] + " " + case["name"]).lower()
    if "whatsapp" in name:
        return "WhatsApp"
    if any(word in name for word in ("browser", "site", "takeover", "signin", "snapshot")):
        return "Browser and takeover"
    if "remind" in name:
        return "Reminders"
    if any(word in name for word in ("agent", "turn", "parallel", "model")):
        return "Conversation and concurrent jobs"
    return "Server, memory and tool contracts"


def proves(case):
    """Test names are evidence identifiers; descriptions stay limited to what tests assert."""
    descriptions = {
        "whatsapp_send_confirms_new_receipt": "Sends the exact text once and confirms a new message ID with a send receipt",
        "whatsapp_read_loaded_messages": "Reads loaded chat messages without sending anything",
        "whatsapp_read_chat_list": "Reads the visible chat list without sending anything",
        "whatsapp_qr_requests_takeover": "Requests QR takeover before trying to open or send a chat",
        "whatsapp_resume_uses_same_page": "Continues on the same page after linking",
        "whatsapp_duplicate_contact_does_not_send": "Does not send when two contacts have the same name",
        "whatsapp_missing_contact_does_not_send": "Does not send to a missing contact",
        "whatsapp_unconfirmed_send_is_not_retried": "Does not retry a send without a new receipt",
        "whatsapp_changed_recipient_does_not_send": "Stops if the selected recipient changes before sending",
        "whatsapp_missing_send_control_does_not_send": "Does not guess when the send button is missing",
        "whatsapp_unready_page_is_not_linked": "An unfinished page load is not mistaken for a linked account",
        "whatsapp_rejects_missing_message": "Rejects an empty contact or message before opening the browser",
        "whatsapp_emits_observed_steps": "Each reported action has a page image captured after it",
        "live_whatsapp_qr_takeover": "The real WhatsApp QR page provides a takeover request and image",
        "live_whatsapp_send_and_read": "A real send receipt and matching message ID confirm the chat round trip",
        "junit_outcomes": "Keeps passes, failures, skips and setup errors distinct",
        "secrets_redacted": "Removes configured secrets and access tokens from saved evidence",
        "environment_precedence": "Preserves explicit environment settings when loading a file",
        "collection_failure_is_error": "A failed or empty test collection is an error",
        "timeout_is_error": "An unfinished suite is an error",
    }
    name, _, variant = case["name"].removeprefix("test_").partition("[")
    if name in descriptions:
        return descriptions[name] + (". Case: " + variant.rstrip("]") if variant else "")
    text = re.sub(r"^test_", "", case["name"]).replace("_", " ")
    return text[0].upper() + text[1:] if text else "Unnamed check"


def reports(data):
    cases = [case for run in data["runs"] for case in run["cases"]]
    counts = {state: sum(c["state"] == state for c in cases) for state in ("passed", "failed", "error", "skipped")}
    executed = counts["passed"] + counts["failed"]
    overview = (f"{executed} tests ran to a test outcome. {counts['passed']} passed, {counts['failed']} failed, "
                f"{counts['error']} errors and {counts['skipped']} skipped. {len(cases)} evidence rows including collection errors.")
    metadata = f"Tested commit {data['commit']}. Window {data['started']} to {data['ended']}."
    method = ("The runner executes the entire unit directory, then e2e, then live, using the same Python environment. "
              "Each row comes from pytest JUnit output, with parameter cases counted separately. WhatsApp checks exercise "
              "the browser wrapper against local pages. E2e checks use the real "
              "model when configured. Live checks require their account credentials. A skip proves nothing about a live service. "
              "Raw sanitized XML, command logs and the run manifest are stored under build/reliability. No count is a success-rate forecast.")
    failures = [f"{proves(c)}: {c['detail']}" for c in cases if c["state"] in ("failed", "error")]
    history = ("The first real WhatsApp visit showed a browser compatibility screen with Playwright's default "
               "headless identity. Using a desktop Chrome identity reached the real QR linking page. The browser lane "
               "was given that finding so linking and takeover use the same persistent session. No message was sent in that probe. "
               "The first local WhatsApp run had 17 passes and one fixture failure: resetting a page redeclared its JavaScript variables. "
               "We scoped the fixture script to each page load, then all 18 WhatsApp checks passed. "
               "The founder removed email and Calendar from scope; their tests are excluded from this report.")
    limits = ["WhatsApp needs one QR linking step and an explicitly configured test contact for a live send; a QR screen is not a sent message.",
              "The integrations are WhatsApp, Lieferando, Uber and LinkedIn. Sign-in walls or blocked pages do not prove completed errands.",
              "This is one run of Ola, a hackathon project by Jayden Bruck. It does not prove phone acceptance or production reliability."]
    escape = lambda value: str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    md = ["# Ola reliability", "", "Ola is a hackathon project by Jayden Bruck.", "", overview, "", metadata, "", "## Method", "", method, ""]
    sections = []
    for group in dict.fromkeys(capability(c) for c in cases):
        rows = [c for c in cases if capability(c) == group]
        md += ["## " + group, "", "| What it proves | How it was run | Result |", "|---|---|---|"]
        table = []
        for case in rows:
            how = case["suite"] + ": " + case["class"].split(".")[-1]
            result = case["state"].upper() + (": " + case["detail"] if case["detail"] else "")
            md.append("| " + " | ".join(escape(v) for v in (proves(case), how, result)) + " |")
            table.append("<tr>" + "".join("<td>" + html.escape(v) + "</td>" for v in (proves(case), how, result)) + "</tr>")
        md.append("")
        sections.append("<h2>" + html.escape(group) + "</h2><table><thead><tr><th>What it proves</th><th>How it was run</th><th>Result</th></tr></thead><tbody>" + "".join(table) + "</tbody></table>")
    md += ["## What failed and what we changed", "", history, ""]
    md += [escape(f) for f in failures] if failures else ["This report's run has no failed test outcomes or collection errors."]
    md += ["", "## Known limits", "", *[line + "  " for line in limits], ""]
    (ROOT / "RELIABILITY.md").write_text("\n".join(md), encoding="utf-8")
    css = """@page { size:A4; margin:18mm 16mm 19mm; @bottom-right { content:counter(page); font:9pt Arial; color:#5A5E66; } }
    :root { --ground:#F6F6F8; --ink:#141519; --secondary:#5A5E66; --hairline:rgba(0,0,0,.08); }
    body { font:10pt/1.45 Arial,sans-serif; color:var(--ink); margin:0; }
    h1 { font-size:30pt; font-weight:500; margin:0 0 8mm; } h2 { font-size:15pt; margin:8mm 0 3mm; break-after:avoid; }
    p { margin:0 0 4mm; } .meta { color:var(--secondary); font-size:9pt; overflow-wrap:anywhere; }
    .summary { background:var(--ground); padding:5mm; margin:5mm 0; font-size:14pt; }
    table { width:100%; border-collapse:collapse; table-layout:fixed; font-size:8.5pt; }
    th { text-align:left; background:var(--ground); font-weight:600; } td,th { padding:2.4mm; border-bottom:1px solid var(--hairline); overflow-wrap:anywhere; vertical-align:top; }
    th:first-child { width:45%; } th:nth-child(2) { width:27%; } tr { break-inside:avoid; } thead { display:table-header-group; }
    """
    document = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Ola reliability</title><style>" + css +
                "</style><body><h1>Ola reliability</h1><p>Ola is a hackathon project by Jayden Bruck.</p><p class='summary'>" + html.escape(overview) +
                "</p><p class='meta'>" + html.escape(metadata) + "</p><h2>Method</h2><p>" + html.escape(method) + "</p>" +
                "".join(sections) + "<h2>What failed and what we changed</h2><p>" + html.escape(history) + "</p>" +
                "".join("<p>" + html.escape(f) + "</p>" for f in failures) + "<h2>Known limits</h2>" +
                "".join("<p>" + html.escape(line) + "</p>" for line in limits) + "</body></html>")
    (OUT / "reliability.html").write_text(document, encoding="utf-8")
    return counts


def pdf():
    chrome = os.getenv("CHROME_PATH") or shutil.which("google-chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    target = ROOT / "RELIABILITY.pdf"
    target.unlink(missing_ok=True)
    run = subprocess.run([chrome, "--headless=new", "--no-pdf-header-footer", "--no-first-run",
                          "--user-data-dir=" + str(OUT / "chrome-profile"), "--print-to-pdf=" + str(target),
                          (OUT / "reliability.html").as_uri()], capture_output=True, timeout=90)
    if run.returncode or not target.exists():
        raise RuntimeError("Chrome PDF rendering failed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--timeout", type=int, default=900, help="Per-suite deadline in seconds")
    parser.add_argument("--render-only", action="store_true", help="Re-render the saved run, without claiming new tests")
    parser.add_argument("--no-pdf", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = OUT / "run.json"
    if args.render_only:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    else:
        env = environment([*args.env_file, ROOT / "server" / ".env", ROOT.parent / ".env.ola-agent"])
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
        data = {"commit": commit + (" + working-tree changes" if dirty else ""), "started": stamp(), "runs": []}
        for suite in ("unit", "e2e", "live"):
            print("Running " + suite, flush=True)
            data["runs"].append(run_suite(suite, env, args.timeout))
        data["ended"] = stamp()
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    counts = reports(data)
    if not args.no_pdf:
        pdf()
    print(json.dumps(counts))
    return int(bool(counts["failed"] or counts["error"]))


if __name__ == "__main__":
    raise SystemExit(main())
