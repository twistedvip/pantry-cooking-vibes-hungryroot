"""Adapt Hungryroot pairings API records to the JSONL ingest contract.

Input: one record from /api/v2/public_pairings/ (the shape ``scrape_pairings``
writes line-per-line to ``data/raw/hungryroot/recipes.jsonl``).

Output: dict matching ``pantry_cooking_vibes.models.RecipeRecord`` so that
``meal-cli ingest --plugin hungryroot`` can validate and UPSERT.
"""

from __future__ import annotations

from pantry_cooking_vibes_hungryroot._nutrition import project_nutrition
from pantry_cooking_vibes_hungryroot._utils import _html_to_text, _to_float, _to_int


def _ingredient_source_key(ing: dict) -> str:
    return str(ing.get("slug") or ing.get("id") or ing.get("name", ""))


def _adapt_ingredient(ing: dict) -> dict:
    name = (ing.get("name") or "").strip()
    brand = (ing.get("brand_name") or "").strip()
    if brand and brand.lower() != "hungryroot":
        original = f"{name} ({brand})".strip()
    else:
        original = name
    return {
        "original_text": original or None,
        "quantity": _to_float(ing.get("amount")),
        "canonical_hint": _ingredient_source_key(ing),
    }


def _adapt_tags(tags: list | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in tags or []:
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def to_contract(raw: dict) -> dict | None:
    """Map an HR pairings record to a JSONL contract dict.

    Returns ``None`` when the record is malformed (missing ``id`` or ``name``)
    so the caller can drop it before validation.
    """
    rid = raw.get("id")
    name = raw.get("name")
    if not rid or not isinstance(name, str) or not name.strip():
        return None

    nutrition = project_nutrition(raw.get("nutrition"))

    rating = _to_float(raw.get("average_rating"))
    if rating is not None and not (0.0 <= rating <= 5.0):
        rating = None

    return {
        "source_id": str(rid),
        "name": name,
        "cooking_time_min": _to_int(raw.get("cooking_time")),
        "servings": _to_int(raw.get("servings")),
        "instructions_md": _html_to_text(raw.get("short_instruction_html")),
        "image_url": raw.get("featured_img_url") or None,
        "rating": rating,
        "rating_count": _to_int(raw.get("num_ratings")),
        "nutrition_json": nutrition,
        "tags": _adapt_tags(raw.get("tags")),
        "ingredients": [_adapt_ingredient(i) for i in (raw.get("ingredients") or [])],
    }
