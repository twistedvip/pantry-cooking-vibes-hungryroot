"""Hungryroot post-process plugin for the pantry-cooking-vibes ingest pipeline.

Registered as ``pantry_cooking_vibes.importers`` entry-point ``hungryroot``.
Invoked by ``meal-cli ingest path/to/hr.jsonl --source hungryroot --plugin hungryroot``
before each record is validated against the JSONL contract.

The scraper writes raw HR API pairings shape; this plugin converts each line
to the contract dict via :func:`_adapter.to_contract` and drops malformed
records so the core validator never sees them.
"""

from __future__ import annotations

from pantry_cooking_vibes_hungryroot import __version__
from pantry_cooking_vibes_hungryroot._adapter import to_contract


class HungryrootImporter:
    """RecipeImporter Protocol implementation for Hungryroot."""

    name = "hungryroot"
    version = __version__

    def post_process(self, records: list[dict]) -> list[dict]:
        """Convert raw HR pairings records to JSONL-contract dicts."""
        out: list[dict] = []
        for raw in records:
            adapted = to_contract(raw)
            if adapted is not None:
                out.append(adapted)
        return out
