"""Tests for the Hungryroot legacy importer: pairings JSONL -> recipes/tags/ingredients."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pantry_cooking_vibes.db import connect

from pantry_cooking_vibes_hungryroot.import_legacy import (
    _ingredient_source_key,
    import_pairings,
)
from pantry_cooking_vibes_hungryroot._utils import _html_to_text

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
        "short_instruction_html": "<p>Heat oil.</p><p>Add chicken &amp; rice.</p>",
        "nutrition": {"calories": 420, "protein": "16.00"},
        "tags": [
            {"id": 181, "name": "Gluten-Free"},
            {"id": 78, "name": "Indian"},
            {"id": 181, "name": "gluten-free"},  # duplicate (case-insensitive)
        ],
        "ingredients": [
            {
                "id": 726,
                "slug": "broccoli-florets-726",
                "name": "Broccoli Florets",
                "brand_name": "Hungryroot",
                "amount": 1.0,
            },
            {
                "id": 1340,
                "slug": "chicken-tikka-masala-saffron-rice-1340",
                "name": "Chicken Tikka Masala + Saffron Rice",
                "brand_name": "Cafe Spice",
                "amount": 2.0,
            },
        ],
    },
    {
        "id": 999001,
        "name": "Simple Salad",
        "slug": "simple-salad-999001",
        "cooking_time": 5,
        "servings": 1,
        "tags": [],
        "ingredients": [
            {
                "id": 726,
                "slug": "broccoli-florets-726",
                "name": "Broccoli Florets",
                "brand_name": "Hungryroot",
                "amount": 0.5,
            },
        ],
    },
    # malformed: should be skipped (no id/name)
    {"slug": "invalid"},
]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    path = tmp_path / "recipes.jsonl"
    _write_jsonl(path, SAMPLE_PAIRINGS)
    return path


def test_html_to_text_strips_tags_and_decodes_entities():
    out = _html_to_text("<p>One.</p><p>Two &amp; three.</p>")
    assert out is not None
    assert "One." in out
    assert "Two & three." in out
    assert "<p>" not in out


def test_html_to_text_handles_none_and_empty():
    assert _html_to_text(None) is None
    assert _html_to_text("") is None
    assert _html_to_text("   ") is None


def test_ingredient_source_key_prefers_slug():
    assert _ingredient_source_key({"slug": "abc-1", "id": 1, "name": "X"}) == "abc-1"
    assert _ingredient_source_key({"id": 7, "name": "X"}) == "7"
    assert _ingredient_source_key({"name": "X"}) == "X"


def test_import_pairings_basic(db_path, jsonl_path):
    stats = import_pairings(jsonl_path=jsonl_path, db_path=db_path, quiet=True)

    assert stats["processed"] == 3
    assert stats["recipes"] == 2
    assert stats["skipped"] == 1
    assert stats["ingredients"] == 3
    assert stats["tags"] == 2  # gluten-free + indian (dedup case-insensitive)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT source_id, name, cooking_time_min, rating, rating_count "
            "FROM recipes ORDER BY source_id"
        ).fetchall()
        assert len(rows) == 2
        first = dict(rows[0])
        assert first["source_id"] == "1067139"
        assert first["cooking_time_min"] == 12
        assert first["rating"] == 1.0
        assert first["rating_count"] == 2138

        tags = [
            r["tag"]
            for r in conn.execute(
                "SELECT tag FROM recipe_tags WHERE recipe_id = "
                "(SELECT id FROM recipes WHERE source_id='1067139') ORDER BY tag"
            ).fetchall()
        ]
        assert tags == ["gluten-free", "indian"]

        ings = conn.execute(
            "SELECT original_text, quantity FROM recipe_ingredients "
            "WHERE recipe_id = (SELECT id FROM recipes WHERE source_id='1067139') "
            "ORDER BY id"
        ).fetchall()
        assert len(ings) == 2
        assert ings[0]["original_text"] == "Broccoli Florets"
        assert ings[1]["original_text"].endswith("(Cafe Spice)")
        assert ings[0]["quantity"] == 1.0


def test_import_pairings_idempotent(db_path, jsonl_path):
    import_pairings(jsonl_path=jsonl_path, db_path=db_path, quiet=True)
    stats = import_pairings(jsonl_path=jsonl_path, db_path=db_path, quiet=True)

    assert stats["recipes"] == 2

    with connect(db_path) as conn:
        recipe_count = conn.execute("SELECT COUNT(*) FROM recipes").fetchone()[0]
        ing_count = conn.execute("SELECT COUNT(*) FROM recipe_ingredients").fetchone()[0]
        tag_count = conn.execute("SELECT COUNT(*) FROM recipe_tags").fetchone()[0]

    assert recipe_count == 2
    assert ing_count == 3
    assert tag_count == 2


def test_import_pairings_uses_canonical_map(db_path, jsonl_path):
    """Approved/proposed mappings in ingredient_mapping_queue should populate canonical_id."""
    with connect(db_path) as conn:
        row = conn.execute(
            "INSERT INTO canonical_ingredients (name, category) "
            "VALUES ('test-broccoli-canonical', 'vegetable') RETURNING id"
        ).fetchone()
        canonical_id = row[0]
        conn.execute(
            """
            INSERT INTO ingredient_mapping_queue
                (source, source_key, original_text, proposed_canonical_id, confidence, status)
            VALUES ('hungryroot_product', 'broccoli-florets-726', 'Broccoli Florets', ?, 0.95, 'approved')
            """,
            (canonical_id,),
        )

    import_pairings(jsonl_path=jsonl_path, db_path=db_path, quiet=True)

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT canonical_id FROM recipe_ingredients WHERE original_text = 'Broccoli Florets'"
        ).fetchall()
        assert len(rows) == 2
        assert all(r["canonical_id"] == canonical_id for r in rows)


def test_import_pairings_missing_file_raises(db_path, tmp_path):
    with pytest.raises(FileNotFoundError):
        import_pairings(jsonl_path=tmp_path / "nope.jsonl", db_path=db_path, quiet=True)


def test_import_pairings_limit(db_path, jsonl_path):
    stats = import_pairings(jsonl_path=jsonl_path, db_path=db_path, limit=1, quiet=True)
    assert stats["recipes"] == 1


# ---------- adapter (raw HR pairings -> JSONL contract) ----------


def test_to_contract_basic_record_passes_validation():
    from pantry_cooking_vibes.models import RecipeRecord

    from pantry_cooking_vibes_hungryroot._adapter import to_contract

    contract = to_contract(SAMPLE_PAIRINGS[0])
    assert contract is not None
    # Round-trips through Pydantic — proves the adapter output is contract-conformant.
    rec = RecipeRecord.model_validate(contract)

    assert rec.source_id == "1067139"
    assert rec.name == "Chicken Tikka Masala + Rice with Broccoli"
    assert rec.cooking_time_min == 12
    assert rec.servings == 4
    assert rec.rating == 1.0
    assert rec.rating_count == 2138
    assert rec.image_url == "https://example.com/img.jpg"
    assert "Heat oil." in (rec.instructions_md or "")
    assert "Two & three." not in (rec.instructions_md or "")  # entity decoded
    assert rec.tags == ["gluten-free", "indian"]
    assert len(rec.ingredients) == 2

    # Brand suffix appended only when brand != "hungryroot".
    originals = [i.original_text for i in rec.ingredients]
    assert "Broccoli Florets" in originals
    assert any(o and o.endswith("(Cafe Spice)") for o in originals)

    # canonical_hint is the slug-or-id key the mapping queue uses.
    hints = [i.canonical_hint for i in rec.ingredients]
    assert "broccoli-florets-726" in hints


def test_to_contract_drops_malformed():
    from pantry_cooking_vibes_hungryroot._adapter import to_contract

    assert to_contract({"slug": "no-id-or-name"}) is None
    assert to_contract({"id": 1, "name": ""}) is None
    assert to_contract({"id": 1}) is None


def test_to_contract_handles_missing_ingredients_and_tags():
    from pantry_cooking_vibes.models import RecipeRecord

    from pantry_cooking_vibes_hungryroot._adapter import to_contract

    contract = to_contract({"id": 42, "name": "Sparse"})
    assert contract is not None
    rec = RecipeRecord.model_validate(contract)
    assert rec.tags == []
    assert rec.ingredients == []


# ---------- plugin (post_process + ingest_jsonl end-to-end) ----------


def test_plugin_post_process_filters_and_adapts():
    from pantry_cooking_vibes_hungryroot.plugin import HungryrootImporter

    out = HungryrootImporter().post_process(list(SAMPLE_PAIRINGS))
    # Two valid + one malformed in SAMPLE_PAIRINGS.
    assert len(out) == 2
    assert all("source_id" in r and "ingredients" in r for r in out)


def test_plugin_via_ingest_jsonl_end_to_end(db_path, jsonl_path):
    """Raw HR pairings JSONL -> ingest_jsonl(plugin='hungryroot') -> DB rows."""
    from pantry_cooking_vibes.db import connect
    from pantry_cooking_vibes.importers.jsonl_ingest import ingest_jsonl

    stats = ingest_jsonl(
        jsonl_path,
        source="hungryroot",
        db_path=db_path,
        plugin="hungryroot",
        quiet=True,
    )

    # Plugin filters the malformed line before validation, so processed counts
    # the contract-shaped records rather than raw lines.
    assert stats["recipes"] == 2
    assert stats["ingredients"] == 3
    assert stats["tags"] == 2
    assert stats["skipped"] == 0

    with connect(db_path) as conn:
        sources = [r["source"] for r in conn.execute("SELECT DISTINCT source FROM recipes")]
        assert sources == ["hungryroot"]
        names = sorted(r["name"] for r in conn.execute("SELECT name FROM recipes"))
        assert names == ["Chicken Tikka Masala + Rice with Broccoli", "Simple Salad"]


def test_plugin_canonical_hint_routes_through_mapping_queue(db_path, jsonl_path):
    """canonical_hint emitted by the adapter should let approved mappings stick."""
    from pantry_cooking_vibes.db import connect
    from pantry_cooking_vibes.importers.jsonl_ingest import ingest_jsonl

    with connect(db_path) as conn:
        canonical_id = conn.execute(
            "INSERT INTO canonical_ingredients (name, category) "
            "VALUES ('test-broccoli', 'vegetable') RETURNING id"
        ).fetchone()[0]
        # source must equal the ingest --source value; mapping queue is keyed
        # on (source, source_key). canonical_hint becomes the source_key.
        conn.execute(
            """
            INSERT INTO ingredient_mapping_queue
                (source, source_key, original_text, proposed_canonical_id, confidence, status)
            VALUES ('hungryroot', 'broccoli-florets-726', 'Broccoli Florets', ?, 0.95, 'approved')
            """,
            (canonical_id,),
        )

    ingest_jsonl(
        jsonl_path,
        source="hungryroot",
        db_path=db_path,
        plugin="hungryroot",
        quiet=True,
    )

    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT canonical_id FROM recipe_ingredients WHERE original_text = 'Broccoli Florets'"
        ).fetchall()
        assert len(rows) == 2
        assert all(r["canonical_id"] == canonical_id for r in rows)
