"""The frozen research artifacts must not change: a change is a new manifest version with a new evaluation (scripts/freeze_research.py)."""

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MANIFESTS = sorted((ROOT / "docs" / "research").glob("*/manifest.json"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _same_content(p: Path, digest: str) -> bool:
    """Some manifests hashed the files as checked out on Windows (CRLF), others as stored (LF); git converts between them. Accept the bytes as they are,
    or with only the line endings changed. Any other difference in content still fails."""
    raw = p.read_bytes()
    lf = raw.replace(b"\r\n", b"\n")
    return any(hashlib.sha256(x).hexdigest() == digest for x in (raw, lf, lf.replace(b"\n", b"\r\n")))


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.name)
def test_every_frozen_artifact_still_matches_its_manifest(manifest: Path):
    m = json.loads(manifest.read_text())
    changed = [rel for rel, meta in m["files"].items() if not _same_content(ROOT / rel, meta["sha256"])]
    assert not changed, (
        f"{changed} changed since {m['version']} was frozen. Do not edit them: create a new version "
        f"(python scripts/freeze_research.py v2) after a new pre-registered evaluation on fresh seeds."
    )


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda p: p.parent.name)
def test_raw_data_that_is_present_matches_its_recorded_hash(manifest: Path):
    m = json.loads(manifest.read_text())
    wrong = []
    for group in m["data"].values():
        for name, digest in group.items():
            p = ROOT / "data" / name if "/" in name else ROOT / "data" / "rows" / name
            if p.exists() and _sha(p) != digest:
                wrong.append(name)
    assert not wrong, f"regenerated data differs from the frozen run for {wrong[:5]}"


def test_the_spent_seed_guards_are_recorded():
    m = json.loads((ROOT / "docs" / "research" / "v1" / "manifest.json").read_text())
    assert all("guard" in e for e in m["experiments"] if e["id"] in ("F1", "D106", "D107"))
