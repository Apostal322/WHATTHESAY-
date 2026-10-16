"""scripts/scan_secrets.py: fakes are assembled at runtime so this file holds no key shapes."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "scan_secrets.py"
spec = importlib.util.spec_from_file_location("scan_secrets", SCRIPT)
assert spec and spec.loader
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)

FAKE_AIZA = "AI" + "za" + "Sy" + "x" * 33
FAKE_AQ = "AQ" + "." + "Ab" + "y" * 40


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        (f"key = '{FAKE_AIZA}'", "google_api_key"),
        (f"auth {FAKE_AQ}", "google_auth_key"),
        ("GEMINI_API_KEY=" + "z" * 24, "secret_assignment"),
        ('$env:BRAVE_SEARCH_API_KEY = "' + "q" * 20 + '"', "secret_assignment"),
        ("-----BEGIN " + "RSA PRIVATE KEY-----", "private_key"),
    ],
)
def test_detects_and_redacts(line: str, kind: str) -> None:
    findings = ss.scan_text("f.txt", [(3, line)])
    assert findings and findings[0].startswith(f"f.txt:3: {kind}")
    assert "x" * 20 not in findings[0] and "y" * 20 not in findings[0]  # never echoed


@pytest.mark.parametrize(
    "line",
    [
        "GEMINI_API_KEY=",  # empty template value
        '$env:GEMINI_API_KEY = Read-Host "Gemini API key" -MaskInput',
        "$env:GEMINI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(",
        "GEMINI_API_KEY=<your key>",
        f"x = '{FAKE_AIZA}'  # " + "secret-scan: allow",
    ],
)
def test_ignores_templates_prompts_and_marked_fakes(line: str) -> None:
    assert ss.scan_text("f.txt", [(1, line)]) == []


def test_staged_mode_sees_added_lines_and_staged_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    (tmp_path / "ok.py").write_text("x = 1\n")
    (tmp_path / "leak.py").write_text("a = 1\nb = '" + FAKE_AQ + "'\n")
    (tmp_path / ".env").write_text("GEMINI_API_KEY=" + "z" * 24 + "\n")
    git("add", "ok.py", "leak.py", ".env")
    monkeypatch.setattr(ss, "REPO", tmp_path)
    findings = ss.staged_findings()
    assert any(f.startswith("leak.py:2: google_auth_key") for f in findings)
    assert any(".env" in f and "staged .env" in f for f in findings)
    assert not any(f.startswith("ok.py") for f in findings)


def test_the_repository_itself_is_clean() -> None:
    assert ss.tracked_findings() == []
