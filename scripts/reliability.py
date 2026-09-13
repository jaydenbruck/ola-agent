"""Run every Python suite and publish measured evidence, including honest skips."""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import uuid
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
    env["OLA_LIVE"] = "1"  # The reliability command explicitly includes the authorized real-site journeys.
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
                if tag == "skipped" and node.get("type") == "pytest.xfail":
                    state = "blocked"
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
    if not cases or (code != 0 and not any(case["state"] in ("failed", "error") for case in cases)):
        cases.append({"suite": suite, "class": "collection", "name": "Suite completion", "state": "error",
                      "detail": f"pytest exit {code}; see build/reliability/{suite}.log", "seconds": 0})
    return {"suite": suite, "command": f"python -m pytest server/tests/{suite} -q --tb=short --junitxml=build/reliability/{suite}.xml",
            "started": started, "ended": stamp(), "exit": code, "cases": cases}


def capability(case):
    if case["suite"] == "swift":
        return "iPhone event parsing and state"
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
        "real_routes_parallel_takeover_resume": "Real HTTP and SSE carry two overlapping browser jobs, chat during takeover, sign-in input, resume and page results",
        "two_jobs_in_parallel_spoken_in_german": "The real model starts two jobs and speaks their controlled lookup results in German",
        "reminder_is_spoken_later": "The real model schedules a reminder that appears later in the conversation",
        "english_request_gets_english_answer": "The real model answers an English request using stored test memory",
        "model_signs_in_via_needs_you_takeover_and_resume": "The real model hands over a local sign-in form and reads the balance after the test member resumes",
        "uber_price_from_home_to_school": "Attempts the requested Uber route; only a displayed fare passes",
        "lieferando_to_the_cart": "Confirms a Margherita and price in the cart, then stops before payment",
        "linkedin_lands_on_the_sign_in_page_never_the_join_page": "Reaches LinkedIn sign-in and offers takeover without entering credentials",
        "kleinanzeigen_search_and_open_a_listing": "Supplemental browser check: opens a Kleinanzeigen listing and reads its price",
        "junit_outcomes": "Keeps passes, failures, skips and setup errors distinct",
        "secrets_redacted": "Removes configured secrets and access tokens from saved evidence",
        "environment_precedence": "Preserves explicit environment settings when loading a file",
        "collection_failure_is_error": "A failed or empty test collection is an error",
        "timeout_is_error": "An unfinished suite is an error",
    }
    name, _, variant = case["name"].removeprefix("test_").partition("[")
    if name in descriptions:
        return descriptions[name] + (". Case: " + variant.rstrip("]") if variant else "")
    text = re.sub(r"^test_?", "", case["name"]).replace("_", " ")
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return text[0].upper() + text[1:] if text else "Unnamed check"


def reports(data):
    cases = [case for run in data["runs"] for case in run["cases"]]
    counts = {state: sum(c["state"] == state for c in cases) for state in ("passed", "failed", "error", "blocked", "skipped")}
    executed = counts["passed"] + counts["failed"] + counts["blocked"]
    overview = (f"{executed} tests ran to a test outcome. {counts['passed']} passed, {counts['failed']} failed, "
                f"{counts['error']} errors, {counts['blocked']} blocked and {counts['skipped']} skipped. {len(cases)} evidence rows in total.")
    metadata = f"Tested commit {data['commit']}. Window {data['started']} to {data['ended']}."
    if data.get("environment"):
        metadata += f" Python {data['environment']['python']} on {data['environment']['platform']}; real-model setting {data['environment']['model']}."
    method = ("The runner executes the entire server unit directory, then e2e, then live, using the same Python environment. "
              "Python rows come from pytest JUnit output, with parameter cases counted separately. Swift rows come from "
              "completed XCTest case lines checked against the suite total, preserving skips. WhatsApp checks exercise "
              "the browser wrapper against local pages. E2e checks use the real "
              "model when configured. Live checks require their account credentials. Blocked means an attempted journey "
              "did not establish its requested outcome; it is not a pass. A skip proves nothing about a live service. "
              "Portable Swift tests then run in an isolated build directory; the real route journey supplies their SSE fixture. "
              "This does not compile or test iOS rendering. Sanitized XML, command logs and the run manifest are stored "
              "under build/reliability. No count is a success-rate forecast.")
    failures = [f"{proves(c)}: {c['detail']}" for c in cases if c["state"] in ("failed", "error")]
    history = ("The first real WhatsApp visit showed a browser compatibility screen with Playwright's default "
               "headless identity. Using a desktop Chrome identity reached the real QR linking page. The browser lane "
               "was given that finding so linking and takeover use the same persistent session. No message was sent in that probe. "
               "The first local WhatsApp run had 17 passes and one fixture failure: resetting a page redeclared its JavaScript variables. "
               "We scoped the fixture script to each page load, then all 18 WhatsApp checks passed. "
               "A later run hit two browser setup errors, including a Playwright driver allocation failure on this shared PC. "
               "The test fixture now reuses one Chromium process with a fresh context for each test. An encoding error in a "
               "German assertion was corrected; all 20 WhatsApp checks then passed. "
               "Swift's parallel xUnit output counted a skipped fixture test as a pass, so the runner now reads serial "
               "XCTest outcomes and checks their count against the suite summary. "
               "The first combined run on ed14410 recorded 117 passes and two failures. A due reminder spoke before "
               "its slow acknowledgement; the core now waits for that acknowledgement before delivering the reminder. "
               "WhatsApp showed an IndexedDB error under the deeply nested Windows pytest profile path. "
               "The same QR check passed with a short isolated workspace profile, which the live test now uses. "
               "The founder removed email and Calendar from scope; their tests are excluded from this report.")
    limits = ["WhatsApp needs one QR linking step and an explicitly configured test contact for a live send; a QR screen is not a sent message.",
              "The integrations are WhatsApp, Lieferando, Uber and LinkedIn. Sign-in walls or blocked pages do not prove completed errands.",
              "This is one run of Ola, a hackathon project by Jayden Bruck. It does not prove phone acceptance or production reliability."]
    escape = lambda value: str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    md = ["# Ola reliability", "", "Ola is a hackathon project by Jayden Bruck.", "", overview, "", metadata, "", "## Method", "", method, ""]
    sections = []
    if data.get("journeys"):
        md += ["## Real-site outcomes", "", "| Site | Observed outcome | Evidence |", "|---|---|---|"]
        rows = []
        for journey in data["journeys"]:
            values = (journey["site"], journey["outcome"], journey["log"])
            md.append("| " + " | ".join(escape(v) for v in values) + " |")
            rows.append("<tr>" + "".join("<td>" + html.escape(v) + "</td>" for v in values) + "</tr>")
        md.append("")
        sections.append("<h2>Real-site outcomes</h2><table class='outcomes'><thead><tr><th>Site</th><th>Observed outcome</th><th>Evidence</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")
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
    .outcomes th:first-child { width:14%; } .outcomes th:nth-child(2) { width:54%; }
    """
    document = ("<!doctype html><html lang='en'><meta charset='utf-8'><title>Ola reliability</title><style>" + css +
                "</style><body><h1>Ola reliability</h1><p>Ola is a hackathon project by Jayden Bruck.</p><p class='summary'>" + html.escape(overview) +
                "</p><p class='meta'>" + html.escape(metadata) + "</p><h2>Method</h2><p>" + html.escape(method) + "</p>" +
                "".join(sections) + "<h2>What failed and what we changed</h2><p>" + html.escape(history) + "</p>" +
                "".join("<p>" + html.escape(f) + "</p>" for f in failures) + "<h2>Known limits</h2>" +
                "".join("<p>" + html.escape(line) + "</p>" for line in limits) + "</body></html>")
    (OUT / "reliability.html").write_text(document, encoding="utf-8")
    return counts


def source_state():
    paths = []
    for flags in (["--cached"], ["--others", "--exclude-standard"]):
        listing = subprocess.check_output(["git", "ls-files", *flags, "--", "server", "scripts", "app"], cwd=ROOT, text=True)
        paths.extend(p for p in listing.splitlines() if Path(p).suffix in (".py", ".toml", ".html", ".css", ".js", ".sh", ".swift") and "/logs/" not in p)
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        full = ROOT / path
        if full.exists():
            digest.update(path.encode() + b"\0" + full.read_bytes() + b"\0")
    return digest.hexdigest()


def run_swift(env, timeout):
    """Use the app's portable package, with xUnit evidence and an isolated WSL directory."""
    started, output, cases, code = stamp(), "", [], 0
    xml_path = OUT / "swift.xml"
    xml_path.unlink(missing_ok=True)
    command = "swift test --package-path app --jobs 1"
    try:
        if platform.system() == "Windows":
            prefix = ["wsl", "-d", env.get("OLA_SWIFT_DISTRO", "CTO-OpenClaw-Proof-20260908"), "--"]
            swift = env.get("OLA_SWIFT_PATH", "/tmp/n3-swift/swift-6.0.3-RELEASE-ubuntu24.04/usr/bin/swift")
            target = "/tmp/ola-reliability-" + uuid.uuid4().hex
            subprocess.run(prefix + ["mkdir", "-p", target], check=True, capture_output=True, timeout=30)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                for name in ("Ola", "Tests", "Package.swift"):
                    archive.add(ROOT / "app" / name, arcname=name)
                if (OUT / "app-events.sse").exists():
                    archive.add(OUT / "app-events.sse", arcname="events.sse")
            subprocess.run(prefix + ["tar", "xzf", "-", "-C", target], input=buffer.getvalue(), check=True, capture_output=True, timeout=60)
            fixture = ["env", "OLA_EVENT_FIXTURE=" + target + "/events.sse"] if (OUT / "app-events.sse").exists() else []
            run = subprocess.run(prefix + fixture + [swift, "test", "--package-path", target, "--jobs", "1"],
                                 capture_output=True, timeout=timeout)
            code, output = run.returncode, (run.stdout + run.stderr).decode("utf-8", "replace")
            command = f"WSL {prefix[2]}: {swift} test --package-path <isolated copy of app> --jobs 1"
        else:
            swift_env = env.copy()
            if (OUT / "app-events.sse").exists():
                swift_env["OLA_EVENT_FIXTURE"] = str(OUT / "app-events.sse")
            run = subprocess.run(["swift", "test", "--package-path", str(ROOT / "app"), "--scratch-path", str(OUT / "swift-build"),
                                  "--jobs", "1"], env=swift_env, capture_output=True, timeout=timeout)
            code, output = run.returncode, (run.stdout + run.stderr).decode("utf-8", "replace")
        cases = read_swift_output(output)
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", name="Swift", tests=str(len(cases)))
        for case in cases:
            element = ET.SubElement(suite, "testcase", classname=case["class"], name=case["name"], time=str(case["seconds"]))
            if case["state"] != "passed":
                ET.SubElement(element, "failure" if case["state"] == "failed" else case["state"], message=case["detail"])
        xml_path.write_text(redact(ET.tostring(root, encoding="unicode"), env), encoding="utf-8")
    except (OSError, subprocess.SubprocessError, ET.ParseError) as error:
        code, output = 125, "Swift test execution did not complete: " + type(error).__name__
    (OUT / "swift.log").write_text(redact(output, env), encoding="utf-8")
    if not cases or (code and not any(c["state"] in ("failed", "error") for c in cases)):
        cases.append({"suite": "swift", "class": "collection", "name": "Swift suite completion", "state": "error",
                      "detail": f"Exit {code}; see build/reliability/swift.log", "seconds": 0})
    return {"suite": "swift", "command": command, "started": started, "ended": stamp(), "exit": code, "cases": cases}


def read_swift_output(output):
    pattern = r"Test Case '([^']+)\.(test[^']+)' (passed|failed|skipped) \(([\d.]+) seconds\)"
    cases = [{"suite": "swift", "class": group, "name": name, "state": state,
              "detail": "See build/reliability/swift.log" if state != "passed" else "", "seconds": float(seconds)}
             for group, name, state, seconds in re.findall(pattern, output)]
    totals = re.findall(r"Executed (\d+) tests?, with", output)
    if not totals or len(cases) != int(totals[-1]):
        cases.append({"suite": "swift", "class": "collection", "name": "Swift outcome count", "state": "error",
                      "detail": "Completed case lines do not match a suite total; see swift.log", "seconds": 0})
    return cases


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
        changes = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", "server", "scripts", "app"], cwd=ROOT, text=True)
        dirty = any(Path(line[3:].strip('"')).suffix in {".py", ".swift", ".toml", ".html", ".css", ".js", ".sh"}
                    for line in changes.splitlines())
        data = {"commit": commit + (" + working-tree changes" if dirty else ""), "source_sha256": source_state(), "started": stamp(), "runs": [],
                "environment": {"python": platform.python_version(), "platform": platform.system(), "model": env.get("OLA_MODEL", "x-ai/grok-4.5")}}
        (OUT / "app-events.sse").unlink(missing_ok=True)
        for suite in ("unit", "e2e", "live"):
            print("Running " + suite, flush=True)
            data["runs"].append(run_suite(suite, env, args.timeout))
        if (ROOT / "app" / "Package.swift").exists():
            print("Running portable Swift tests", flush=True)
            data["runs"].append(run_swift(env, args.timeout))
        data["ended"] = stamp()
        data["journeys"] = []
        for path in sorted((ROOT / "server" / "tests" / "live" / "logs").glob("*.md")):
            if path.stat().st_mtime < datetime.fromisoformat(data["started"]).timestamp():
                continue
            content = redact(path.read_text(encoding="utf-8"), env)
            if "## Outcome" in content:
                data["journeys"].append({"site": path.stem.split("-")[0], "log": path.relative_to(ROOT).as_posix(),
                                         "outcome": content.split("## Outcome", 1)[1].strip()})
        manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")
    counts = reports(data)
    if not args.no_pdf:
        pdf()
    evidence = ROOT / "server" / "tests" / "live" / "evidence"
    evidence.mkdir(exist_ok=True)
    (evidence / "latest.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    if (OUT / "app-events.sse").exists():
        shutil.copyfile(OUT / "app-events.sse", evidence / "events.sse")
    print(json.dumps(counts))
    return int(bool(counts["failed"] or counts["error"]))


if __name__ == "__main__":
    raise SystemExit(main())
