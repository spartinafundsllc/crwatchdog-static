#!/usr/bin/env python3
"""
replace_all_site_links.py

Replace all Geniuslink URLs across the entire CRWatchdog website with
canonical direct Amazon affiliate URLs (?tag=crwatchdog-20) or targeted
Amazon search fallbacks.
"""

import os
import re
import json
import time

from urllib.parse import urlparse, parse_qs, unquote, quote_plus

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP_FILE = os.path.join(PROJECT_ROOT, "_agent_context", "genius_to_amazon_map.json")
AMAZON_TAG = "crwatchdog-20"

# Regex to find any geni.us link
GENIUS_FIND_RE = re.compile(r'https?://(?:[a-zA-Z0-9-]+\.)?geni\.us/[^\s\"\'\<\>\)]+')
ASIN_RE = re.compile(r'(?:/dp/|/gp/product/|/)([B0-9][A-Z0-9]{9})(?:/|\?|$)', re.IGNORECASE)

def sanitize_destination(dest):
    if not dest:
        return dest
    # Handle embedded amazon URL inside amazon-adsystem redirects
    if "amazon-adsystem.com" in dest:
        sub_idx = dest.find("https://www.amazon.com")
        if sub_idx != -1:
            dest = dest[sub_idx:]

    parsed = urlparse(dest)
    netloc = parsed.netloc.lower()

    asin_match = ASIN_RE.search(dest)
    if asin_match and "amazon." in netloc:
        asin = asin_match.group(1).upper()
        return f"https://www.amazon.com/dp/{asin}?tag={AMAZON_TAG}"

    if "amazon." in netloc and (parsed.path.startswith("/s") or "field-keywords" in dest or "keywords=" in dest or "k=" in dest or "lp_query=" in dest):
        qs = parse_qs(parsed.query)
        keywords = qs.get("k", qs.get("field-keywords", qs.get("keywords", qs.get("lp_query", []))))
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

    return dest

def load_replacement_map():
    if not os.path.exists(MAP_FILE):
        raise FileNotFoundError(f"Mapping file not found: {MAP_FILE}")

    with open(MAP_FILE, "r", encoding="utf-8") as f:
        raw_map = json.load(f)

    # Sort keys by length descending so longer compound keys match first
    sorted_keys = sorted(raw_map.keys(), key=lambda k: len(k), reverse=True)

    full_map = {}
    for k in sorted_keys:
        dest = sanitize_destination(raw_map[k])
        full_map[k] = dest
        # Ensure both http and https versions exist
        if k.startswith("http://"):
            full_map["https://" + k[7:]] = dest
        elif k.startswith("https://"):
            full_map["http://" + k[8:]] = dest

    return full_map

def replace_in_content(content, full_map):
    replacements_made = 0

    def replacer(match):
        nonlocal replacements_made
        raw_url = match.group(0)

        # 1. Exact match
        if raw_url in full_map:
            replacements_made += 1
            return full_map[raw_url]

        # 2. Check for trailing HTML entity like &lt; or &gt;
        prefix = raw_url
        suffix = ""
        for entity in ("&lt;", "&gt;", "&quot;", "&amp;"):
            if entity in raw_url:
                idx = raw_url.index(entity)
                prefix = raw_url[:idx]
                suffix = raw_url[idx:]
                break

        # 3. Strip trailing punctuation
        core_url = prefix.rstrip(".,;:!\"'“”’″`)\\]")
        punct = prefix[len(core_url):]

        if core_url in full_map:
            replacements_made += 1
            return full_map[core_url] + punct + suffix

        # Unmatched fallback - return original
        print(f"    [WARNING] Unmatched link: {raw_url}")
        return raw_url

    new_content = GENIUS_FIND_RE.sub(replacer, content)
    return new_content, replacements_made

def main():
    start_time = time.time()
    full_map = load_replacement_map()
    print(f">>> Loaded replacement map with {len(full_map)} lookup variations.")

    total_files_scanned = 0
    files_modified = 0
    total_replacements = 0

    EXCLUDED_DIRS = {"_site", "node_modules", ".git", "_agent_context", ".gemini", "brain"}
    TARGET_EXTS = {".html", ".md", ".json", ".xml"}

    print(">>> Scanning and updating files across site...")
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in TARGET_EXTS:
                continue

            filepath = os.path.join(root, f)
            # Skip cache and map files themselves
            if f in ("genius_to_amazon_map.json", ".geniuslink_cache.json"):
                continue

            total_files_scanned += 1

            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as fp:
                    original = fp.read()

                if "geni.us" not in original:
                    continue

                updated, count = replace_in_content(original, full_map)
                if count > 0 and updated != original:
                    with open(filepath, "w", encoding="utf-8") as fp:
                        fp.write(updated)
                    files_modified += 1
                    total_replacements += count
            except Exception as e:
                print(f"Error processing {filepath}: {e}")

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"REPLACEMENT SUMMARY:")
    print(f"  Total files scanned:       {total_files_scanned}")
    print(f"  Files modified:            {files_modified}")
    print(f"  Total links replaced:      {total_replacements}")
    print(f"  Time taken:                {elapsed:.2f}s")
    print("=" * 60)

    # Post-replacement verification scan
    print("\n>>> Performing verification scan for remaining geni.us links...")
    remaining_links = []
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in TARGET_EXTS or f in (".geniuslink_cache.json", "genius_to_amazon_map.json"):
                continue
            filepath = os.path.join(root, f)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as fp:
                    c = fp.read()
                matches = GENIUS_FIND_RE.findall(c)
                if matches:
                    rel_p = os.path.relpath(filepath, PROJECT_ROOT)
                    remaining_links.append((rel_p, matches))
            except Exception:
                pass

    if not remaining_links:
        print(">>> SUCCESS! Exactly 0 geni.us links remain across all content files.")
    else:
        print(f">>> CAUTION: Found {len(remaining_links)} files still containing geni.us links:")
        for rel_p, m in remaining_links[:10]:
            print(f"  {rel_p}: {m}")

if __name__ == "__main__":
    main()
