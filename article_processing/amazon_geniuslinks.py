#!/usr/bin/env python3
"""
replace_amazon_with_geniuslinks.py

Reads a Markdown file, replaces Amazon links with Geniuslink short URLs via the Geniuslink API,
and writes the updated Markdown.

Required env vars:
  GENIUSLINK_API_KEY
  GENIUSLINK_API_SECRET
  GENIUSLINK_GROUP_ID   (e.g. 13082)

Usage:
  python replace_amazon_with_geniuslinks.py input.md
  python replace_amazon_with_geniuslinks.py input.md -o output.md

Default output:
  If -o/--output is not provided, the script appends ".updated" to the input filename,
  e.g. "post.md" -> "post.md.updated"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests


GENIUSLINK_BASE = "https://api.geni.us"
CREATE_ENDPOINT = "/v3/shorturls"

# Markdown patterns:
MD_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\((\s*<?)([^)\s>]+)(>?\s*)\)")
MD_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
BARE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>()\"']+)")

# Amazon detection
AMAZON_HOST_RE = re.compile(
    r"(^|\.)("
    r"amazon\.[a-z.]{2,}"
    r"|amzn\.to"
    r"|a\.co"
    r")$",
    re.IGNORECASE,
)
ALREADY_GENIUS_RE = re.compile(r"(^|\.)((geni\.us)|(buy\.geni\.us))$", re.IGNORECASE)


@dataclass(frozen=True)
class Replacement:
    start: int
    end: int
    old_url: str
    new_url: str


def is_amazon_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.split("@")[-1].split(":")[0].lower()
    except Exception:
        return False
    if not host:
        return False
    return AMAZON_HOST_RE.search(host) is not None


def is_already_genius(url: str) -> bool:
    try:
        host = urlparse(url).netloc.split("@")[-1].split(":")[0].lower()
    except Exception:
        return False
    if not host:
        return False
    return ALREADY_GENIUS_RE.search(host) is not None


def strip_trailing_punct(url: str) -> str:
    return url.rstrip(".,;:)]}!?")


def iter_urls_in_markdown(md: str) -> Iterable[Tuple[int, int, str]]:
    # Inline: [text](url)
    for m in MD_INLINE_LINK_RE.finditer(md):
        url = m.group(3)
        yield (m.start(3), m.end(3), url)

    # Autolinks: <https://...>
    for m in MD_AUTOLINK_RE.finditer(md):
        url = m.group(1)
        yield (m.start(1), m.end(1), url)

    # Bare URLs:
    for m in BARE_URL_RE.finditer(md):
        raw = m.group("url")
        cleaned = strip_trailing_punct(raw)
        yield (m.start("url"), m.start("url") + len(cleaned), cleaned)


def load_cache(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(path: Path, cache: Dict[str, str]) -> None:
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def extract_short_url_from_response(data) -> Optional[str]:
    """
    Geniuslink create-shorturl responses often look like:

      { "shortUrl": { "domain": "geni.us", "code": "OOK1x", ... } }

    i.e., they do NOT necessarily return a full "https://..." URL.
    We construct it as: https://{domain}/{code}

    We also support other common shapes (direct string, etc.)
    """
    if isinstance(data, dict):
        # Direct string fields
        for k in ("ShortUrl", "shortUrl", "short_url", "url", "href"):
            v = data.get(k)
            if isinstance(v, str) and v.startswith("http"):
                return v

        # Nested object under "shortUrl"
        su = data.get("shortUrl") or data.get("ShortUrl")
        if isinstance(su, dict):
            # Full URL maybe present
            for k in ("url", "Url", "shortUrl", "ShortUrl", "href"):
                v = su.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    return v

            domain = su.get("domain") or su.get("baseDomain")
            code = su.get("code") or su.get("baseCode")
            if isinstance(domain, str) and isinstance(code, str) and domain and code:
                return f"https://{domain}/{code}"

        # Other nesting
        for container_key in ("data", "result"):
            v = data.get(container_key)
            inner = extract_short_url_from_response(v)
            if inner:
                return inner

        # Arrays
        for container_key in ("items", "results"):
            v = data.get(container_key)
            if isinstance(v, list):
                for item in v:
                    inner = extract_short_url_from_response(item)
                    if inner:
                        return inner

    return None


class GeniuslinkClient:
    def __init__(self, api_key: str, api_secret: str, group_id: int, impersonate: Optional[str] = None, timeout_s: int = 30):
        self.api_key = api_key
        self.api_secret = api_secret
        self.group_id = int(group_id)
        self.impersonate = impersonate
        self.timeout_s = timeout_s
        self.session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        h = {
            "X-Api-Key": self.api_key,
            "X-Api-Secret": self.api_secret,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.impersonate:
            h["X-Impersonate"] = self.impersonate
        return h

    def create_shorturl(self, destination_url: str, debug: bool = False) -> str:
        endpoint = f"{GENIUSLINK_BASE}{CREATE_ENDPOINT}"
        payload = {"Url": destination_url, "GroupId": self.group_id}

        for attempt in range(1, 5):
            resp = self.session.post(endpoint, headers=self._headers(), json=payload, timeout=self.timeout_s)

            if debug:
                try:
                    dbg = resp.json()
                except Exception:
                    dbg = resp.text
                print(f"\n[DEBUG] attempt={attempt} status={resp.status_code}\n{json.dumps(dbg, indent=2) if isinstance(dbg, dict) else dbg}\n", file=sys.stderr)

            # Handle rate limiting / transient failures
            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                sleep_s = float(retry_after) if (retry_after and retry_after.isdigit()) else min(2 ** attempt, 10)
                time.sleep(sleep_s)
                continue

            if resp.status_code >= 400:
                # show the body to help diagnose
                raise RuntimeError(f"Geniuslink API error {resp.status_code}: {resp.text[:800]}")

            data = resp.json()
            short = extract_short_url_from_response(data)
            if short:
                return short

            raise RuntimeError(f"Geniuslink response did not include a usable short URL. Keys: {list(data)[:30]}")

        raise RuntimeError("Failed to create Geniuslink short URL after retries.")


def apply_replacements(text: str, replacements: List[Replacement]) -> str:
    out = text
    for r in sorted(replacements, key=lambda x: x.start, reverse=True):
        out = out[:r.start] + r.new_url + out[r.end:]
    return out


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_updated{input_path.suffix}")



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path, help="Input markdown file")
    ap.add_argument("-o", "--output", type=Path, default=None, help="Output markdown file (default: <input>.updated)")
    ap.add_argument("--cache", type=Path, default=Path(".geniuslink_cache.json"), help="Cache file for URL mappings")
    ap.add_argument("--impersonate", type=str, default=None, help="Optional Geniuslink sub-account username")
    ap.add_argument("--debug", action="store_true", help="Print Geniuslink API JSON responses on stderr")
    ap.add_argument("--dry-run", action="store_true", help="Don't call the API; just report matches")
    args = ap.parse_args()

    if not args.input.exists():
        print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
        return 2

    out_path = args.output or default_output_path(args.input)
    md = args.input.read_text(encoding="utf-8")

    cache = load_cache(args.cache)
    spans = list(iter_urls_in_markdown(md))

    uniq_urls: Dict[str, None] = {}
    for _, _, u in spans:
        uniq_urls[strip_trailing_punct(u)] = None

    amazon_urls = [u for u in uniq_urls.keys() if is_amazon_url(u) and not is_already_genius(u)]

    if args.dry_run:
        print(f"Found {len(amazon_urls)} Amazon URLs to replace.")
        for u in amazon_urls[:100]:
            print("  ", u)
        return 0

    api_key = os.getenv("GENIUSLINK_API_KEY")
    api_secret = os.getenv("GENIUSLINK_API_SECRET")
    group_id = os.getenv("GENIUSLINK_GROUP_ID")

    if not api_key or not api_secret:
        print("ERROR: Set GENIUSLINK_API_KEY and GENIUSLINK_API_SECRET.", file=sys.stderr)
        return 2
    if not group_id:
        print('ERROR: Set GENIUSLINK_GROUP_ID (e.g. export GENIUSLINK_GROUP_ID="13082").', file=sys.stderr)
        return 2

    try:
        group_id_int = int(group_id)
    except ValueError:
        print(f"ERROR: GENIUSLINK_GROUP_ID must be an integer (got {group_id!r}).", file=sys.stderr)
        return 2

    client = GeniuslinkClient(api_key=api_key, api_secret=api_secret, group_id=group_id_int, impersonate=args.impersonate)

    mapping: Dict[str, str] = {}
    for u in amazon_urls:
        if u in cache:
            mapping[u] = cache[u]
            continue
        short = client.create_shorturl(u, debug=args.debug)
        mapping[u] = short
        cache[u] = short
        time.sleep(0.05)

    reps: List[Replacement] = []
    for start, end, raw_url in spans:
        old = strip_trailing_punct(raw_url)
        new = mapping.get(old)
        if not new:
            continue
        reps.append(Replacement(start=start, end=end, old_url=old, new_url=new))

    updated = apply_replacements(md, reps)
    out_path.write_text(updated, encoding="utf-8")
    save_cache(args.cache, cache)

    print(f"Replaced {len(reps)} URL occurrences ({len(mapping)} unique Amazon URLs).")
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
