"""Exercise the real chat/SSE routes and capture bytes for the Swift parser tests.

No mock model. No messages to external people. Never prints credentials.
Run: python app/Tools/probe.py --env-file <server env> --url http://127.0.0.1:8787
Then: OLA_EVENT_FIXTURE=<absolute events.sse path> swift test --package-path app
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import uuid

import httpx


async def run(args):
    token = os.environ.get("OLA_TOKEN", "")
    if args.env_file:
        for line in Path(args.env_file).read_text(encoding="utf-8-sig").splitlines():
            if line.startswith("OLA_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not token:
        raise SystemExit("OLA_TOKEN is required in the environment or env file")
    thread = "n3-probe-" + uuid.uuid4().hex
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    records = []
    raw = bytearray()
    ready = asyncio.Event()
    done = asyncio.Event()
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(base_url=args.url, headers=headers, timeout=180) as client:
        health = await client.get("/health")
        health.raise_for_status()
        assert (await client.get("/jobs", headers={"Authorization": "Bearer invalid-probe"})).status_code == 401

        async def collect():
            async with client.stream("GET", "/events/" + thread) as response:
                response.raise_for_status()
                ready.set()
                async for line in response.aiter_lines():
                    raw.extend((line + "\n").encode())
                    if line.startswith("data:"):
                        event = json.loads(line[5:].strip())
                        records.append(event)
                        if event["type"] == "assistant.done":
                            raw.extend(b"\n")
                            done.set()
                            return

        listener = asyncio.create_task(collect())
        try:
            await asyncio.wait_for(ready.wait(), 20)
            sent = await client.post("/chat", json={"thread_id": thread, "text": "Antworte auf Deutsch: Schreibe einen kurzen Satz mit dem Wort **Hallo** in Fettdruck und danach genau zwei Stichpunkte über Regen. Nutze keine Werkzeuge."})
            assert sent.status_code == 202, "Chat did not accept the message"
            turn = sent.json()["turn_id"]
            await asyncio.wait_for(done.wait(), 180)
            await listener
        finally:
            listener.cancel()
            await asyncio.gather(listener, return_exceptions=True)
        assert any(e["type"] == "assistant.delta" for e in records), "No streamed text"
        assert records[-1]["type"] == "assistant.done", "No completion"
        assert records[-1]["turn_id"] == turn, "Wrong turn completed"
        assert records[-1].get("text"), "No authoritative completion text"
        sequences = [e["seq"] for e in records]
        assert sequences == sorted(set(sequences)), "Events duplicated or out of order"
        replay = []
        async with client.stream("GET", "/events/" + thread, headers={"Last-Event-ID": str(sequences[0])}) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:].strip())
                    replay.append(event["seq"])
                    if event["type"] == "assistant.done":
                        break
        assert replay == sequences[1:], "Reconnect replay differs"
        jobs = await client.get("/jobs", params={"thread_id": thread, "all": 1})
        jobs.raise_for_status()
        assert isinstance(jobs.json(), list)
    (output / "events.sse").write_bytes(raw)
    summary = {"chat_accepted": True, "streamed_events": len(records), "completion_has_full_text": True,
               "replayed_events": len(replay), "auth_rejected": True, "jobs_route": True,
               "fixture": str((output / "events.sse").resolve())}
    (output / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file")
    parser.add_argument("--url", default="http://127.0.0.1:8787")
    parser.add_argument("--output", default="app/.evidence")
    asyncio.run(run(parser.parse_args()))
