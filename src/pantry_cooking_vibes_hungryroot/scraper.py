"""
Hungryroot API scrapers: pairings (recipes) and products.

Pairings endpoint:  /api/v2/public_pairings/   (64k+ recipes)
Products endpoint:  /api/v2/public_products/    (~888 products, ingredient SKUs)

Both use standard DRF pagination: count/next/results.
State files under data/raw/hungryroot/ allow resumable scrapes.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

UA = "pantry-cooking-vibes-hungryroot/0.1 (+local-research)"

PAIRINGS_BASE = "https://www.hungryroot.com/api/v2/public_pairings/"
PRODUCTS_BASE = "https://www.hungryroot.com/api/v2/public_products/"

_REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = _REPO_ROOT / "data" / "raw" / "hungryroot"


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json"})
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically: tmp file in same dir + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _fetch_page(session: requests.Session, base: str, offset: int, limit: int) -> dict:
    url = f"{base}?{urlencode({'limit': limit, 'offset': offset})}"
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _scrape(
    base_url: str,
    out_path: Path,
    state_path: Path,
    *,
    limit: int = 500,
    sleep: float = 1.0,
    max_pages: int = 0,
    resume: bool = True,
    verbose: bool = True,
) -> int:
    """Generic paginated scraper. Returns total records written this run."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    offset = 0
    total_written = 0
    if resume and state_path.exists():
        try:
            state = json.loads(state_path.read_text())
            offset = int(state.get("offset", 0))
            total_written = int(state.get("total_written", 0))
            if verbose and offset:
                print(
                    f"[hungryroot] resuming from offset={offset} "
                    f"(already written: {total_written})",
                    file=sys.stderr,
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    session = _make_session()
    page = 0
    expected_total: int | None = None
    mode = "a" if (resume and offset > 0) else "w"

    with out_path.open(mode, encoding="utf-8") as fh:
        while True:
            page += 1
            data = _fetch_page(session, base_url, offset, limit)

            if expected_total is None:
                expected_total = data.get("count")
                if verbose:
                    print(
                        f"[hungryroot] API reports total={expected_total}",
                        file=sys.stderr,
                    )

            results = data.get("results", [])
            for rec in results:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
            total_written += len(results)
            offset += len(results)

            if verbose:
                print(
                    f"[hungryroot] page {page}: got={len(results)} total_written={total_written}",
                    file=sys.stderr,
                )

            _atomic_write_text(
                state_path,
                json.dumps({"offset": offset, "total_written": total_written}),
            )

            if not data.get("next") or not results:
                break
            if max_pages and page >= max_pages:
                break

            time.sleep(sleep)

    return total_written


def scrape_pairings(
    out_path: Path | None = None,
    state_path: Path | None = None,
    *,
    limit: int = 500,
    sleep: float = 1.0,
    max_pages: int = 0,
    resume: bool = True,
    verbose: bool = True,
) -> int:
    """Scrape all HR recipe pairings to a JSONL file. Returns records written."""
    out = out_path or RAW_DIR / "recipes.jsonl"
    state = state_path or RAW_DIR / "pairings_state.json"
    return _scrape(
        PAIRINGS_BASE,
        out,
        state,
        limit=limit,
        sleep=sleep,
        max_pages=max_pages,
        resume=resume,
        verbose=verbose,
    )


def scrape_products(
    out_path: Path | None = None,
    state_path: Path | None = None,
    *,
    limit: int = 100,
    sleep: float = 1.0,
    max_pages: int = 0,
    resume: bool = True,
    verbose: bool = True,
) -> int:
    """Scrape all HR products (~888) to products.jsonl. Returns records written."""
    out = out_path or RAW_DIR / "products.jsonl"
    state = state_path or RAW_DIR / "products_state.json"
    return _scrape(
        PRODUCTS_BASE,
        out,
        state,
        limit=limit,
        sleep=sleep,
        max_pages=max_pages,
        resume=resume,
        verbose=verbose,
    )
