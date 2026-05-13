"""Tests for the raw-HR-pairings -> contract-JSONL pre-transform."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pantry_cooking_vibes_hungryroot.transform import main, transform_jsonl

SAMPLE_PAIRINGS = [
    {
        "id": 1067139,
        "name": "Chicken Tikka Masala + Rice with Broccoli",
        "slug": "chicken-tikka-masala-rice-with-broccoli-1067139",
        "cooking_time": 12,
        "servings": 4,
        "average_rating": "1.00",
        "num_ratings": 2138,
        "featured_img_url": "https://example.com/img.jpg",
        "short_instruction_html": "<p>Heat oil.</p>",
        "tags": [{"id": 1, "name": "Indian"}],
        "ingredients": [
            {
                "id": 726,
                "slug": "broccoli-florets-726",
                "name": "Broccoli Florets",
                "brand_name": "Hungryroot",
                "amount": 1.0,
            }
        ],
    },
    {
        "id": 999001,
        "name": "Simple Salad",
        "featured_img_url": "https://example.com/salad.jpg",
        "ingredients": [],
    },
    # malformed: dropped by adapter
    {"slug": "no-id-or-name"},
]


def _write_raw(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


@pytest.fixture
def raw_path(tmp_path: Path) -> Path:
    p = tmp_path / "raw.jsonl"
    _write_raw(p, SAMPLE_PAIRINGS)
    return p


def test_transform_writes_contract_jsonl(raw_path: Path, tmp_path: Path):
    dst = tmp_path / "out.jsonl"
    stats = transform_jsonl(raw_path, dst, quiet=True)

    assert stats == {"processed": 3, "written": 2, "dropped": 1, "malformed": 0}

    lines = dst.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["source_id"] == "1067139"
    assert parsed[0]["image_url"] == "https://example.com/img.jpg"
    assert parsed[0]["tags"] == ["indian"]
    assert parsed[1]["source_id"] == "999001"


def test_transform_round_trips_through_ingest(raw_path: Path, tmp_path: Path, db_path):
    """Pre-transformed JSONL ingests cleanly without --plugin."""
    from pantry_cooking_vibes.db import connect
    from pantry_cooking_vibes.importers.jsonl_ingest import ingest_jsonl

    dst = tmp_path / "contract.jsonl"
    transform_jsonl(raw_path, dst, quiet=True)

    stats = ingest_jsonl(dst, source="hungryroot", db_path=db_path, quiet=True)
    assert stats["recipes"] == 2
    assert stats["skipped"] == 0

    with connect(db_path) as conn:
        rows = sorted(r["source_id"] for r in conn.execute("SELECT source_id FROM recipes"))
    assert rows == ["1067139", "999001"]


def test_transform_handles_malformed_json(tmp_path: Path):
    src = tmp_path / "raw.jsonl"
    src.write_text(
        '{"id": 1, "name": "ok", "ingredients": []}\n'
        "not-json\n"
        '{"id": 2, "name": "ok2", "ingredients": []}\n',
        encoding="utf-8",
    )
    dst = tmp_path / "out.jsonl"
    stats = transform_jsonl(src, dst, quiet=True)
    assert stats == {"processed": 3, "written": 2, "dropped": 0, "malformed": 1}


def test_transform_missing_src_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        transform_jsonl(tmp_path / "nope.jsonl", tmp_path / "out.jsonl", quiet=True)


def test_cli_main_returns_zero(raw_path: Path, tmp_path: Path, capsys):
    dst = tmp_path / "out.jsonl"
    rc = main([str(raw_path), str(dst), "--quiet"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "transform done" in out
    assert dst.exists()


def test_cli_main_missing_src_returns_one(tmp_path: Path, capsys):
    rc = main([str(tmp_path / "nope.jsonl"), str(tmp_path / "out.jsonl"), "--quiet"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "input not found" in err
