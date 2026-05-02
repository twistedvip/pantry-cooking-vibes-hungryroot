# pantry-cooking-vibes-hungryroot

Hungryroot recipe scraper + post-process plugin for the
`pantry-cooking-vibes` core.

## Endpoints

- `https://www.hungryroot.com/api/v2/public_pairings/` — 64k+ recipes (pairings)
- `https://www.hungryroot.com/api/v2/public_products/` — ~888 products (ingredient SKUs)

Both use DRF pagination (`count`/`next`/`results`). State files under
`data/raw/hungryroot/` allow resumable scrapes.

## Layout

```
src/pantry_cooking_vibes_hungryroot/
  scraper.py         # paginated API scraper -> raw JSONL
  _adapter.py        # raw HR pairings record -> JSONL contract dict
  plugin.py          # RecipeImporter entry-point: filters + adapts each line
  import_legacy.py   # legacy direct-DB importer (pre-JSONL contract)
  _utils.py          # HTML/coercion helpers (copied to keep repo standalone)
  _nutrition.py      # macro projection helper
tests/
  test_scraper.py    # adapter, plugin, end-to-end ingest_jsonl coverage
```

## Pipeline

1. `scrape_pairings()` writes raw API records (one per line) to
   `data/raw/hungryroot/recipes.jsonl`.
2. `meal-cli ingest data/raw/hungryroot/recipes.jsonl --source hungryroot --plugin hungryroot`
   loads `HungryrootImporter`, runs `_adapter.to_contract` on each line, and
   UPSERTs into core's `recipes` / `recipe_tags` / `recipe_ingredients`.

The adapter:

- skips records missing `id` or `name`
- maps `cooking_time` → `cooking_time_min`, `featured_img_url` → `image_url`,
  `average_rating` → `rating` (clamped to 0–5)
- decodes `short_instruction_html` to plain text
- projects HR's verbose nutrition into `{calories, protein_g, ...}`
- tags: deduped lowercase
- ingredients: brand suffix appended only when `brand_name` ≠ `Hungryroot`;
  `canonical_hint` = slug-or-id so the mapping queue can resolve canonicals

## Status

`import_legacy.py` is retained for operators with raw HR JSONL captured under
the old direct-DB importer. New pipelines should use `meal-cli ingest`.

## Local install (dev)

```bash
uv pip install -e path/to/pantry-cooking-vibes   # install core
uv pip install -e .                              # install this plugin (registers entry-point)
```

Then in core:

```bash
meal-cli ingest path/to/hungryroot.jsonl --source hungryroot --plugin hungryroot
```
