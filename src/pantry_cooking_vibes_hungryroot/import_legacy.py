"""Legacy direct-DB importer for Hungryroot pairings JSONL.

Pre-dates the JSONL contract in ``pantry-cooking-vibes`` core. Kept for
operators who already have raw HR API JSONL on disk; new pipelines should
convert to the contract and use ``meal-cli ingest`` instead.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from pantry_cooking_vibes.db import DB_PATH, connect

from pantry_cooking_vibes_hungryroot._nutrition import project_nutrition
from pantry_cooking_vibes_hungryroot._utils import _html_to_text, _to_float, _to_int
from pantry_cooking_vibes_hungryroot.scraper import RAW_DIR


def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _ingredient_source_key(ing: dict) -> str:
    return str(ing.get("slug") or ing.get("id") or ing.get("name", ""))


def _load_canonical_map(conn: sqlite3.Connection) -> dict[str, int]:
    """Map HR product source_key -> canonical_id from ingredient_mapping_queue."""
    rows = conn.execute(
        """
        SELECT source_key, proposed_canonical_id
        FROM ingredient_mapping_queue
        WHERE source = 'hungryroot_product'
          AND status IN ('approved', 'proposed')
          AND proposed_canonical_id IS NOT NULL
        """
    ).fetchall()
    return {r["source_key"]: r["proposed_canonical_id"] for r in rows}


def _upsert_recipe(conn: sqlite3.Connection, rec: dict) -> int:
    source_id = str(rec.get("id"))
    nutrition = project_nutrition(rec.get("nutrition"))
    cur = conn.execute(
        """
        INSERT INTO recipes
            (source, source_id, name, cooking_time_min, servings,
             instructions_md, nutrition_json, image_url, rating, rating_count)
        VALUES ('hungryroot', ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_id) DO UPDATE SET
            name             = excluded.name,
            cooking_time_min = excluded.cooking_time_min,
            servings         = excluded.servings,
            instructions_md  = excluded.instructions_md,
            nutrition_json   = excluded.nutrition_json,
            image_url        = excluded.image_url,
            rating           = excluded.rating,
            rating_count     = excluded.rating_count
        RETURNING id
        """,
        (
            source_id,
            rec.get("name") or "(untitled)",
            _to_int(rec.get("cooking_time")),
            _to_int(rec.get("servings")),
            _html_to_text(rec.get("short_instruction_html")),
            json.dumps(nutrition, ensure_ascii=False) if nutrition else None,
            rec.get("featured_img_url"),
            _to_float(rec.get("average_rating")),
            _to_int(rec.get("num_ratings")),
        ),
    )
    row = cur.fetchone()
    return row["id"]


def _replace_tags(conn: sqlite3.Connection, recipe_id: int, tags: list[dict]) -> int:
    conn.execute("DELETE FROM recipe_tags WHERE recipe_id = ?", (recipe_id,))
    seen: set[str] = set()
    inserted = 0
    for t in tags or []:
        name = (t.get("name") or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        conn.execute(
            "INSERT OR IGNORE INTO recipe_tags (recipe_id, tag) VALUES (?, ?)",
            (recipe_id, name),
        )
        inserted += 1
    return inserted


def _replace_ingredients(
    conn: sqlite3.Connection,
    recipe_id: int,
    ingredients: list[dict],
    canonical_map: dict[str, int],
) -> int:
    conn.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    inserted = 0
    for ing in ingredients or []:
        key = _ingredient_source_key(ing)
        canonical_id = canonical_map.get(key)
        original = (ing.get("name") or "").strip()
        brand = (ing.get("brand_name") or "").strip()
        if brand and brand.lower() != "hungryroot":
            original = f"{original} ({brand})".strip()
        conn.execute(
            """
            INSERT INTO recipe_ingredients
                (recipe_id, canonical_id, original_text, quantity, unit, notes)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                recipe_id,
                canonical_id,
                original or None,
                _to_float(ing.get("amount")),
                None,
                None,
            ),
        )
        inserted += 1
    return inserted


def import_pairings(
    jsonl_path: Path | None = None,
    *,
    db_path: Path | None = None,
    batch_size: int = 200,
    limit: int = 0,
    quiet: bool = False,
) -> dict:
    """Import recipes from a Hungryroot pairings JSONL file.

    Idempotent: re-running upserts existing recipes by (source, source_id);
    tags and ingredients are replaced wholesale per recipe.
    """
    path = jsonl_path or (RAW_DIR / "recipes.jsonl")
    db = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(path)

    stats = {"processed": 0, "recipes": 0, "ingredients": 0, "tags": 0, "skipped": 0}

    with connect(db) as conn:
        canonical_map = _load_canonical_map(conn)
        for rec in _iter_jsonl(path):
            stats["processed"] += 1
            if not rec.get("id") or not rec.get("name"):
                stats["skipped"] += 1
                continue
            recipe_id = _upsert_recipe(conn, rec)
            stats["tags"] += _replace_tags(conn, recipe_id, rec.get("tags") or [])
            stats["ingredients"] += _replace_ingredients(
                conn, recipe_id, rec.get("ingredients") or [], canonical_map
            )
            stats["recipes"] += 1

            if stats["recipes"] % batch_size == 0:
                conn.commit()
                if not quiet:
                    print(f"  imported {stats['recipes']} recipes...")
            if limit and stats["recipes"] >= limit:
                break

    return stats
