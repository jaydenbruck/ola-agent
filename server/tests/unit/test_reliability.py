"""Evidence handling must not turn missing suites, skips or errors into passes."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

spec = importlib.util.spec_from_file_location("reliability", Path(__file__).resolve().parents[3] / "scripts" / "reliability.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_junit_outcomes(tmp_path):
    path = tmp_path / "run.xml"
    path.write_text('<testsuites><testsuite><testcase name="ok"/><testcase name="no"><skipped message="Missing account"/></testcase>'
                    '<testcase name="bad"><failure message="Mismatch"/></testcase><testcase name="setup"><error message="Setup failed"/></testcase>'
                    '</testsuite></testsuites>')
    assert [c["state"] for c in report.read_cases(path, "live")] == ["passed", "skipped", "failed", "error"]


def test_secrets_redacted():
    env = {"OPENROUTER_API_KEY": "sensitive-key", "OLA_WHATSAPP_TEST_CONTACT": "person@example.test", "EXAMPLE_REFRESH_TOKEN": "refresh-me"}
    assert report.redact("sensitive-key person@example.test refresh-me Bearer access-value", env) == "[redacted] [redacted] [redacted] Bearer [redacted]"


def test_environment_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("OLA_MODEL", "existing")
    path = tmp_path / "credentials"
    path.write_text('OLA_MODEL=file\nOLA_EXAMPLE="quoted"\n# ignored\n')
    env = report.environment([path])
    assert env["OLA_MODEL"] == "existing" and env["OLA_EXAMPLE"] == "quoted"


@pytest.mark.parametrize("code", [2, 3, 4, 5])
def test_collection_failure_is_error(tmp_path, monkeypatch, code):
    monkeypatch.setattr(report, "OUT", tmp_path)
    monkeypatch.setattr(report.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], code, "collection failed", ""))
    result = report.run_suite("e2e", {}, 1)
    assert result["cases"][0]["state"] == "error"


def test_timeout_is_error(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "OUT", tmp_path)

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("pytest", 1)

    monkeypatch.setattr(report.subprocess, "run", timeout)
    assert report.run_suite("live", {}, 1)["exit"] == 124
