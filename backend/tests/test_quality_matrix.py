"""docs/quality/quality-matrix.json stays well formed and its evidence exists.

A PASS must name evidence; every evidence path must exist in the repository, and a
`path::test_name` reference must name a test that exists in that file.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MATRIX = json.loads((REPO / "docs" / "quality" / "quality-matrix.json").read_text("utf-8"))
FIELDS = {
    "id", "area", "priority", "status", "validation", "requirement", "implementation", "test",
    "observed", "evidence", "limitation", "owner",
}  # fmt: skip


def test_every_check_is_complete_and_uses_known_statuses() -> None:
    statuses = set(MATRIX["status_legend"])
    ids = [c["id"] for c in MATRIX["checks"]]
    assert len(ids) == len(set(ids)), "duplicate check ids"
    for check in MATRIX["checks"]:
        assert set(check) == FIELDS, check["id"]
        assert check["status"] in statuses, check["id"]
        assert check["validation"] in MATRIX["validation_levels"], check["id"]
        assert check["priority"] in {"P0", "P1", "P2", "P3"}, check["id"]
        if check["status"] == "PASS":
            assert check["evidence"], f"{check['id']}: PASS without evidence"
            assert check["validation"] != "none", check["id"]


def test_evidence_exists() -> None:
    for check in MATRIX["checks"]:
        for ref in check["evidence"]:
            path, _, test_name = ref.partition("::")
            assert (REPO / path).exists(), f"{check['id']}: missing {path}"
            if test_name:
                text = (REPO / path).read_text("utf-8")
                assert f"def {test_name}(" in text, f"{check['id']}: no test {test_name}"
