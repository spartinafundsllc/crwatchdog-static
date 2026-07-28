#!/usr/bin/env python3
"""
resolve_amazon_search_links.py

Scans a Markdown file for Amazon search URLs (https://www.amazon.com/s?k=...),
fetches the top matching product ASIN from Amazon, converts the search URL
to a direct product URL (https://www.amazon.com/dp/<ASIN>), and generates a Geniuslink.

Usage:
    python resolve_amazon_search_links.py input.md [-o output.md]
"""

import sys
import os
import re
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    import amazon_geniuslinks
except ImportError:
    amazon_geniuslinks = None

def fetch_top_asin_from_amazon_search(query):
    """
    Extracts the top product ASIN for a search query from Amazon.
    """
    encoded_query = urllib.parse.quote(query)
    url = f"https://www.amazon.com/s?k={encoded_query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            asins = re.findall(r'data-asin=["\']([B0-9][A-Z0-9]{9})["\']', html)
            asins = [a for a in asins if a]
            if asins:
                return asins[0]
    except Exception as e:
        print(f"Error fetching ASIN for search query '{query}': {e}", file=sys.stderr)
    return None

def resolve_search_links_in_markdown(md_content, convert_genius=True):
    """
    Finds all https://www.amazon.com/s?k=... links, resolves them to https://www.amazon.com/dp/<ASIN>,
    and converts them to Geniuslinks if credentials are available.
    """
    search_link_pattern = re.compile(r'https?://(?:www\.)?amazon\.[a-z\.]+/s\?[^)\s>"]+')
    matches = set(search_link_pattern.findall(md_content))
    
    if not matches:
        print("No search links found in content.")
        return md_content
        
    print(f"Found {len(matches)} search links to resolve.")
    
    # Initialize Geniuslink client if needed
    gl_client = None
    if convert_genius and amazon_geniuslinks:
        api_key = os.getenv("GENIUSLINK_API_KEY")
        api_secret = os.getenv("GENIUSLINK_API_SECRET")
        group_id = os.getenv("GENIUSLINK_GROUP_ID", "140645")
        if api_key and api_secret:
            gl_client = amazon_geniuslinks.GeniuslinkClient(api_key, api_secret, int(group_id))
            
    cache_path = Path(".geniuslink_cache.json")
    if not cache_path.exists():
        cache_path = Path("article_processing/.geniuslink_cache.json")
        
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    updated_md = md_content

    for link in matches:
        parsed = urllib.parse.urlparse(link)
        params = urllib.parse.parse_qs(parsed.query)
        query_list = params.get("k", [])
        if not query_list:
            continue
            
        query = query_list[0]
        print(f"\nResolving search link for query: '{query}'...")
        asin = fetch_top_asin_from_amazon_search(query)
        
        if not asin:
            print(f"  Could not find ASIN for '{query}'. Skipping.")
            continue
            
        direct_url = f"https://www.amazon.com/dp/{asin}"
        print(f"  Resolved to direct ASIN URL: {direct_url}")
        
        final_url = direct_url
        if gl_client:
            try:
                genius_url = gl_client.create_shorturl(direct_url)
                print(f"  Converted Geniuslink: {genius_url}")
                final_url = genius_url
                cache[direct_url] = genius_url
            except Exception as e:
                print(f"  Geniuslink conversion error: {e}")

        # Replace search link with resolved URL in markdown
        updated_md = updated_md.replace(link, final_url)
        time.sleep(1)

    # Save updated geniuslink cache
    try:
        cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        Path("article_processing/.geniuslink_cache.json").write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"Failed to update cache: {e}")

    return updated_md

def main():
    if len(sys.argv) < 2:
        print("Usage: python resolve_amazon_search_links.py <input.md> [-o output.md]")
        sys.exit(1)
        
    input_path = Path(sys.argv[1])
    output_path = input_path
    
    if len(sys.argv) >= 4 and sys.argv[2] in ("-o", "--output"):
        output_path = Path(sys.argv[3])
        
    if not input_path.exists():
        print(f"Error: {input_path} not found.")
        sys.exit(1)
        
    content = input_path.read_text(encoding="utf-8")
    resolved_content = resolve_search_links_in_markdown(content)
    
    output_path.write_text(resolved_content, encoding="utf-8")
    print(f"\nSUCCESS! Updated: {output_path}")

if __name__ == "__main__":
    main()
