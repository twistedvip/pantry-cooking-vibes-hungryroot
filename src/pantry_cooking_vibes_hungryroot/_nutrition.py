"""Project verbose source nutrition dicts into a compact macro dict.

Hungryroot's API returns ~30 fields per recipe (raw values + DV percentages +
serving boilerplate), averaging ~650 bytes per recipe. The meal planner only
needs the macro totals. This helper shrinks that to ~150 bytes while preserving
the fields we actually surface (calories, protein, fat, carbs, fiber, sodium).
"""

from __future__ import annotations

from typing import Any


def _num(v: Any) -> float | None:
    """Coerce ``"10.00"`` / ``10`` / ``"  "`` / ``None`` to ``float | None``."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_COMPACT_FIELDS = ("calories", "protein_g", "fat_g", "carbs_g", "fiber_g", "sodium_mg")


def _strip_unit(v: Any) -> Any:
    """Schema.org nutrition values are often ``"12 g"`` or ``"250 mg"``. Strip the unit."""
    if isinstance(v, str):
        return v.split()[0] if v.strip() else v
    return v


def project_nutrition(src: Any) -> dict | None:
    """Return a compact ``{calories, protein_g, fat_g, carbs_g, fiber_g, sodium_mg}``
    dict, or ``None`` when the input is missing/empty.
    """
    if not isinstance(src, dict) or not src:
        return None

    if "protein_g" in src or "sodium_mg" in src:
        compact = {k: src.get(k) for k in _COMPACT_FIELDS}
    else:
        compact = {
            "calories": _num(src.get("calories") or src.get("calorieContent")),
            "protein_g": _num(src.get("protein") or _strip_unit(src.get("proteinContent"))),
            "fat_g": _num(src.get("total_fat") or _strip_unit(src.get("fatContent"))),
            "carbs_g": _num(src.get("total_carb") or _strip_unit(src.get("carbohydrateContent"))),
            "fiber_g": _num(src.get("fiber") or _strip_unit(src.get("fiberContent"))),
            "sodium_mg": _num(src.get("sodium") or _strip_unit(src.get("sodiumContent"))),
        }
    if all(v is None for v in compact.values()):
        return None
    return compact
