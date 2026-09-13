"""Run portable Swift tests in the existing WSL test environment, without host mounts."""
import io
from pathlib import Path
import subprocess
import tarfile

root = Path(__file__).resolve().parents[1]
distro = "CTO-OpenClaw-Proof-20260908"
prefix = ["wsl", "-d", distro, "--"]
target = "/tmp/n3-swift/project"
swift = "/tmp/n3-swift/swift-6.0.3-RELEASE-ubuntu24.04/usr/bin/swift"
subprocess.run(prefix + ["mkdir", "-p", target], check=True)
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
    for name in ["Ola", "Tests", "Package.swift"]:
        archive.add(root / name, arcname=name)
    fixture = root / ".evidence" / "events.sse"
    if fixture.exists():
        archive.add(fixture, arcname="events.sse")
subprocess.run(prefix + ["tar", "xzf", "-", "-C", target], input=buffer.getvalue(), check=True)
command = prefix + (["env", "OLA_EVENT_FIXTURE=" + target + "/events.sse"] if fixture.exists() else [])
result = subprocess.run(command + [swift, "test", "--package-path", target, "--scratch-path", "/tmp/n3-swift/build"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
print(result.stdout.decode(errors="replace"))
(root / ".evidence").mkdir(exist_ok=True)
(root / ".evidence" / "swift-tests.log").write_bytes(result.stdout)
raise SystemExit(result.returncode)
