"""Short company descriptions for the symbol autocomplete.

Alpaca's asset list carries only a legal name ("IREN Limited Ordinary Shares") —
there is no business description in it at all, which is why this exists. yfinance
supplies `longBusinessSummary`; this module condenses it to one line and caches
the result on disk permanently.

SCOPE — this is cosmetic UI text and nothing else. It is never consulted by the
trading engine, the scanner or the risk layer, and every failure path degrades to
an empty string so a row simply shows no description. That matters because
yfinance is an UNOFFICIAL Yahoo scraper: it can rate-limit or change shape
without notice. Anything that could affect a trading decision must not be sourced
from here.

Caching is permanent rather than TTL'd: a company's line of business effectively
does not change, and re-fetching would spend a scarce, unofficial rate limit to
learn nothing. Delete data/company_profiles.json to force a refresh.
"""

import json
import logging
import os
import re
import threading

logger = logging.getLogger(__name__)

CACHE_PATH = "data/company_profiles.json"
MAX_LEN = 132          # one comfortable line in the dropdown
FETCH_TIMEOUT_S = 4.0  # per symbol; a slow lookup must not stall the request

# A period that ends a SENTENCE, not an abbreviation. Without these guards,
# "SoFi Technologies, Inc. provides various financial services…" truncates at
# "Inc." and the description degrades to just the company name again.
_ABBR_GUARD = (r"(?<!\bInc)(?<!\bLtd)(?<!\bCorp)(?<!\bCo)(?<!\bplc)(?<!\bLLC)"
               r"(?<!\bL\.P)(?<!\bS\.A)(?<!\bN\.V)(?<!\bA\.G)(?<!\bJr)(?<!\bSt)")
_SENTENCE_END = re.compile(_ABBR_GUARD + r"\.\s+[A-Z(]")

# Summaries almost always open with the legal name ("IREN Limited operates in…").
# Stripping it makes the row read "IREN  operates in…" instead of repeating the
# name that is already in the column to its left.
_VERB_LEAD = re.compile(
    r"^.{0,70}?\s+(operates|provides|develops|designs|engages|focuses|manufactures|"
    r"offers|owns|produces|invests|seeks|distributes|operates as|is\s)", re.I)

_lock = threading.Lock()
_cache = None


def _load():
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    try:
        if os.path.exists(CACHE_PATH):
            with open(CACHE_PATH, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                _cache = {k: str(v) for k, v in data.items()}
    except Exception as e:
        logger.warning("Could not read %s (%s); starting with an empty cache.", CACHE_PATH, e)
    return _cache


def _save():
    try:
        os.makedirs(os.path.dirname(CACHE_PATH) or ".", exist_ok=True)
        with open(CACHE_PATH, "w") as f:
            json.dump(_cache, f, indent=0, sort_keys=True)
    except Exception as e:
        logger.warning("Could not write %s: %s", CACHE_PATH, e)


def condense(summary: str) -> str:
    """One-line description from a full business summary."""
    text = re.sub(r"\s+", " ", (summary or "").strip())
    if not text:
        return ""
    m = _SENTENCE_END.search(text)
    if m:
        text = text[:m.start() + 1]
    m = _VERB_LEAD.match(text)
    if m:
        text = text[m.start(1):]
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


def _fetch(symbol: str) -> str:
    """One yfinance lookup. Returns '' on any failure — never raises."""
    try:
        import yfinance as yf
        info = yf.Ticker(symbol).get_info()
        return condense(info.get("longBusinessSummary"))
    except Exception as e:
        logger.debug("No profile for %s: %s: %s", symbol, type(e).__name__, e)
        return ""


def get_cached(symbols) -> dict:
    """Descriptions already on disk. Pure lookup — never hits the network."""
    cache = _load()
    return {s: cache[s] for s in symbols if s in cache and cache[s]}


def fetch_missing(symbols, limit: int = 8) -> dict:
    """Fetch up to `limit` uncached symbols and return everything now known.

    The cap is deliberate: the dropdown can show 15 rows and each lookup costs
    ~0.3s, so an uncapped call would make a keystroke feel like a hang. Whatever
    is left over gets picked up by a later request, by which point the earlier
    ones are cached and instant.

    An empty result is cached too — a symbol with no Yahoo profile (many ETFs)
    should be asked about once, not on every keystroke forever.
    """
    cache = _load()
    missing = [s for s in symbols if s not in cache][:limit]
    if missing:
        for sym in missing:
            cache[sym] = _fetch(sym)
        with _lock:
            _save()
    return {s: cache[s] for s in symbols if cache.get(s)}


def prefetch_async(symbols) -> None:
    """Warm the cache in the background (used for watchlist symbols at startup).

    Daemon thread so it can never hold up shutdown, and it only touches symbols
    that are not already cached, so a restart with a warm cache costs nothing.
    """
    cache = _load()
    todo = [s for s in symbols if s not in cache]
    if not todo:
        return

    def _run():
        for sym in todo:
            cache[sym] = _fetch(sym)
        with _lock:
            _save()
        logger.info("Company profiles prefetched for %d symbol(s).", len(todo))

    threading.Thread(target=_run, daemon=True, name="profile-prefetch").start()
