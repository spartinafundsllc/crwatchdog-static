#!/usr/bin/env python3
"""
resolve_all_geniuslinks.py

Paced background resolver for Geniuslink -> Amazon affiliate URLs for CRWatchdog.
Operates steadily at ~20 requests per minute to stay comfortably within
Geniuslink's rate limits, automatically handling 429 backoff, live logging
to stdout with unbuffered flushes, and auto-saving progress every 10 items.
"""

import os
import re
import glob
import json
import time
import sys
from urllib.parse import urlparse, parse_qs, unquote, quote_plus
import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUTPUT_MAP_FILE = os.path.join(os.path.dirname(__file__), "genius_to_amazon_map.json")
AMAZON_TAG = "crwatchdog-20"

GENIUS_RE = re.compile(r'https?://(?:buy\.)?geni\.us/([a-zA-Z0-9_\-]+)', re.IGNORECASE)
ASIN_RE = re.compile(r'(?:/dp/|/gp/product/|/)([B0-9][A-Z0-9]{9})(?:/|\?|$)', re.IGNORECASE)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9'
}

def clean_amazon_url(destination_url, code):
    parsed = urlparse(destination_url)
    netloc = parsed.netloc.lower()

    # If it redirected to another geni.us link, return as is so resolver can follow
    if "geni.us" in netloc:
        return destination_url

    # Check for ASIN in destination
    asin_match = ASIN_RE.search(destination_url)
    if asin_match and "amazon." in netloc:
        asin = asin_match.group(1).upper()
        return f"https://www.amazon.com/dp/{asin}?tag={AMAZON_TAG}"

    # Check if it's an Amazon search URL
    if "amazon." in netloc and (parsed.path.startswith("/s") or "field-keywords" in destination_url or "keywords=" in destination_url or "k=" in destination_url):
        qs = parse_qs(parsed.query)
        keywords = qs.get("k", qs.get("field-keywords", qs.get("keywords", [])))
        if keywords and keywords[0]:
            kw = unquote(keywords[0]).strip()
            return f"https://www.amazon.com/s?k={quote_plus(kw)}&tag={AMAZON_TAG}"
        return f"https://www.amazon.com/s?tag={AMAZON_TAG}"

    if "amazon." in netloc:
        qs = parse_qs(parsed.query)
        qs["tag"] = [AMAZON_TAG]
        qs.pop("geniuslink", None)
        qs.pop("th", None)
        qs.pop("psc", None)
        query_parts = [f"{k}={quote_plus(v)}" for k, vals in qs.items() for v in vals]
        clean_query = "&".join(query_parts)
        return f"https://www.amazon.com{parsed.path}?{clean_query}" if clean_query else f"https://www.amazon.com{parsed.path}?tag={AMAZON_TAG}"

    return destination_url

def resolve_url(session, url, depth=0):
    if depth > 5:
        return url, "MAX_DEPTH_EXCEEDED"

    m = GENIUS_RE.match(url)
    code = m.group(1) if m else ""

    while True:
        try:
            r = session.get(url, headers=HEADERS, allow_redirects=False, timeout=10)
            if r.status_code == 429:
                print(f"    [Rate-Limited 429] Pausing 65 seconds for Geniuslink rate limit reset...", flush=True)
                time.sleep(65)
                continue

            loc = r.headers.get("Location")
            if loc:
                # If chained geni.us link, recursively resolve
                if "geni.us" in loc:
                    time.sleep(2.0)
                    return resolve_url(session, loc, depth + 1)
                target = clean_amazon_url(loc, code)
                return target, "RESOLVED_302"
            elif r.status_code == 200:
                meta_match = re.search(r'url=([^\s\"\'\<\>]+)', r.text, re.IGNORECASE)
                if meta_match:
                    meta_url = meta_match.group(1)
                    if "geni.us" in meta_url:
                        time.sleep(2.0)
                        return resolve_url(session, meta_url, depth + 1)
                    target = clean_amazon_url(meta_url, code)
                    return target, "RESOLVED_META"
                return url, "STATUS_200_NO_REDIRECT"
            elif r.status_code in (404, 410):
                # Expired or dead link on geni.us, fall back to Amazon search by code
                return f"https://www.amazon.com/s?k={quote_plus(code)}&tag={AMAZON_TAG}", "DEAD_FALLBACK"
            else:
                return url, f"STATUS_{r.status_code}"
        except Exception as e:
            print(f"    [Network Exception] {e}. Retrying in 5s...", flush=True)
            time.sleep(5)

def prefill_from_caches(resolved_map):
    cache_files = [
        os.path.join(PROJECT_ROOT, ".geniuslink_cache.json"),
        os.path.join(PROJECT_ROOT, "article_processing", ".geniuslink_cache.json")
    ]
    added = 0
    for cf in cache_files:
        if os.path.exists(cf):
            try:
                with open(cf, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                for amz_url, genius_url in cdata.items():
                    m = GENIUS_RE.match(genius_url)
                    if not m:
                        continue
                    code = m.group(1)
                    cleaned = clean_amazon_url(amz_url, code)
                    # Store both http and https versions of the geni.us url
                    http_v = f"http://geni.us/{code}"
                    https_v = f"https://geni.us/{code}"
                    if http_v not in resolved_map:
                        resolved_map[http_v] = cleaned
                        added += 1
                    if https_v not in resolved_map:
                        resolved_map[https_v] = cleaned
                        added += 1
            except Exception as e:
                print(f"Notice reading {cf}: {e}")
    if added > 0:
        print(f">>> Pre-populated {added} entries from existing .geniuslink_cache.json")

def find_all_genius_urls():
    urls = set()
    all_files = []
    for ext in ("*.html", "*.md", "*.json"):
        all_files.extend(glob.glob(os.path.join(PROJECT_ROOT, "**", ext), recursive=True))

    for path in all_files:
        rel = os.path.relpath(path, PROJECT_ROOT)
        if rel.startswith("_site") or rel.startswith("node_modules") or ".git" in rel or "_agent_context" in rel:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            for m in GENIUS_RE.finditer(content):
                urls.add(m.group(0))
        except Exception:
            pass

    return sorted(list(urls))

def main():
    os.makedirs(os.path.dirname(OUTPUT_MAP_FILE), exist_ok=True)
    urls = find_all_genius_urls()
    print(f">>> Total unique geni.us URLs found in codebase: {len(urls)}", flush=True)

    # Load existing cache
    resolved_map = {}
    if os.path.exists(OUTPUT_MAP_FILE):
        try:
            with open(OUTPUT_MAP_FILE, "r", encoding="utf-8") as f:
                resolved_map = json.load(f)
        except Exception:
            pass

    # Pre-populate from .geniuslink_cache.json
    prefill_from_caches(resolved_map)

    # Filter to remaining URLs
    to_resolve = []
    for u in urls:
        m = GENIUS_RE.match(u)
        code = m.group(1) if m else ""
        http_u = f"http://geni.us/{code}"
        https_u = f"https://geni.us/{code}"
        # If neither or incomplete, resolve
        if http_u not in resolved_map and https_u not in resolved_map:
            to_resolve.append(u)
        elif resolved_map.get(http_u) == http_u or resolved_map.get(https_u) == https_u:
            to_resolve.append(u)

    already_done = len(urls) - len(to_resolve)
    print(f">>> Already resolved: {already_done}/{len(urls)}. Remaining: {len(to_resolve)}", flush=True)

    if not to_resolve:
        print(">>> All links are already fully resolved!", flush=True)
        with open(OUTPUT_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(resolved_map, f, indent=2, sort_keys=True)
        return

    est_minutes = (len(to_resolve) * 2.8) / 60
    print(f">>> Starting paced resolution (~21 req/min, steady). Estimated time: ~{est_minutes:.1f} minutes.", flush=True)

    session = requests.Session()
    start_time = time.time()

    for idx, url in enumerate(to_resolve, 1):
        target, status = resolve_url(session, url)
        m = GENIUS_RE.match(url)
        code = m.group(1) if m else ""
        resolved_map[f"http://geni.us/{code}"] = target
        resolved_map[f"https://geni.us/{code}"] = target

        # Pacing: sleep 2.7 seconds between requests to maintain ~21 req/min
        time.sleep(2.7)

        # Live progress output
        if idx % 5 == 0 or idx == len(to_resolve):
            elapsed = time.time() - start_time
            rate = idx / elapsed if elapsed > 0 else 0
            rem_items = len(to_resolve) - idx
            rem_min = (rem_items / rate) / 60 if rate > 0 else 0
            pct = (idx / len(to_resolve)) * 100
            print(f"[{idx}/{len(to_resolve)}] ({pct:.1f}%) | {url} -> {target[:65]}... | ETA: {rem_min:.1f}m", flush=True)

        # Save to disk every 10 items
        if idx % 10 == 0 or idx == len(to_resolve):
            with open(OUTPUT_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(resolved_map, f, indent=2, sort_keys=True)

    print(f"\n>>> COMPLETE! All links resolved. Saved to {OUTPUT_MAP_FILE}", flush=True)

if __name__ == "__main__":
    main()
