"""Read-only simulator takeover journey, limited to its own generated thread."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

base = os.environ["SIMCTL_CHILD_OLA_SMOKE_SERVER"].rstrip("/")
thread = os.environ["SIMCTL_CHILD_OLA_SMOKE_THREAD_ID"]
token = os.environ["SIMCTL_CHILD_OLA_SMOKE_TOKEN"]
evidence = Path("build/screenshots/takeover.json")


def call(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + "/" + path.lstrip("/"), data=data,
                                     headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def jobs():
    rows = call("jobs?" + urllib.parse.urlencode({"thread_id": thread, "all": 1}))
    assert all(row["thread_id"] == thread for row in rows), "Wrong thread in jobs response"
    return rows


def selected():
    saved = json.loads(evidence.read_text())
    return next(row for row in jobs() if row["job_id"] == saved["job_id"])


if sys.argv[1] == "cleanup":
    for row in jobs():
        if row["state"] in ["running", "needs_you"]:
            call(f"jobs/{row['job_id']}/cancel", {})
    print("Smoke thread cleanup completed.")
elif sys.argv[1] == "wait":
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        rows = jobs()
        match = next((row for row in rows if row["state"] == "needs_you" and row.get("frame_url")), None)
        if match:
            evidence.write_text(json.dumps({key: match.get(key) for key in ["job_id", "thread_id", "title", "state", "frame_url"]}, indent=2))
            print("Real browser job is waiting with a frame; capture the app's takeover view.")
            break
        if rows and all(row["state"] in ["done", "failed", "cancelled"] for row in rows):
            raise SystemExit("Job ended before the required takeover; no takeover proof")
        time.sleep(1)
    else:
        raise SystemExit("No real takeover frame arrived within 90 seconds")
elif sys.argv[1] == "scroll":
    row = selected()
    result = call(f"jobs/{row['job_id']}/input", {"kind": "scroll", "dy": 420})
    assert result.get("ok") is True, "Scroll was rejected"
    print("Remote scroll accepted; the still-open app must update its frame.")
elif sys.argv[1] == "finish":
    row = selected()
    if row["state"] == "needs_you":
        call(f"jobs/{row['job_id']}/resume", {})
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        row = selected()
        if row["state"] in ["done", "failed", "cancelled"]:
            break
        time.sleep(1)
    if row["state"] in ["running", "needs_you"]:
        call(f"jobs/{row['job_id']}/cancel", {})
        raise SystemExit("Resumed job did not finish; only this smoke job was cancelled")
    saved = json.loads(evidence.read_text())
    saved["final_state"] = row["state"]
    evidence.write_text(json.dumps(saved, indent=2))
    assert row["state"] == "done", "Resumed job failed"
    print("Resumed browser job finished. No sign-in, account creation, or message was requested.")
