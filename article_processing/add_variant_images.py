#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import requests
from pathlib import Path
from urllib.parse import urlparse, quote
from dotenv import load_dotenv
from amazon_paapi import AmazonApi

# Suppress deprecation warnings
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

load_dotenv()

def get_env_var(name, required=True):
    val = os.getenv(name)
    if required and not val:
        print(f"ERROR: Missing environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return val

def extract_asin(url):
    match = re.search(r"(?:/dp/|/gp/product/|/)([B0-9][A-Z0-9]{9})(?:/|\?|$)", url)
    if match:
        return match.group(1)
    return None

def sanitize_filename(title, max_len=120):
    import unicodedata
    t = unicodedata.normalize("NFKD", title)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^\w\s\-.,()&+]", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        t = "amazon_product"
    if len(t) > max_len:
        t = t[:max_len].rstrip()
    return t

def download_image(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "CRWatchdog-Publisher/1.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    path.write_bytes(r.content)

def main():
    if len(sys.argv) < 2:
        print("Usage: python add_variant_images.py <post_file.md>")
        sys.exit(1)
        
    post_path = Path(sys.argv[1])
    if not post_path.exists():
        print(f"Error: {post_path} not found.")
        sys.exit(1)
        
    # Read the post
    content = post_path.read_text(encoding="utf-8")
    
    # Load geniuslink cache to reverse map geniuslinks to amazon urls
    cache_path = Path(".geniuslink_cache.json")
    if not cache_path.exists():
        cache_path = Path("article_processing/.geniuslink_cache.json")
        
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Warning: Failed to load geniuslink cache: {e}")
            cache = {}
    else:
        cache = {}
        
    # Reverse cache mapping: geniuslink -> amazon_url
    rev_cache = {v: k for k, v in cache.items()}
    
    # Find all Geniuslinks and Amazon links in the content
    # Look for geni.us URLs first, and fall back to amazon URLs if present
    genius_pattern = re.compile(r"https://geni\.us/[a-zA-Z0-9]+")
    amazon_pattern = re.compile(r"https?://(?:www\.)?amazon\.[a-z\.]+/[\w\-/]+")
    
    all_links = set(genius_pattern.findall(content) + amazon_pattern.findall(content))
    print(f"Found {len(all_links)} product links to analyze.")
    
    # Setup PA API client
    access_key = get_env_var("AMAZON_ACCESS_KEY")
    secret_key = get_env_var("AMAZON_SECRET_KEY")
    tag = get_env_var("AMAZON_TAG")
    region = os.getenv("AMAZON_REGION", "US")
    
    try:
        amazon_api = AmazonApi(access_key, secret_key, tag, region)
    except Exception as e:
        print(f"ERROR: Failed to initialize Amazon API: {e}", file=sys.stderr)
        sys.exit(1)
        
    images_dir = Path("src/markdown_images")
    if not images_dir.exists():
        images_dir = Path("_site/markdown_images") # Fallback
    images_dir.mkdir(parents=True, exist_ok=True)
    
    updated_content = content
    
    for link in all_links:
        # Resolve geniuslink to amazon_url
        amazon_url = rev_cache.get(link, link)
        asin = extract_asin(amazon_url)
        
        if not asin:
            print(f"Skipping link (no ASIN found): {link}")
            continue
            
        print(f"\nProcessing ASIN: {asin} for link: {link}...")
        
        # Fetch variant images
        item = None
        for attempt in range(1, 4):
            try:
                items = amazon_api.get_items(asin)
                if items:
                    item = items[0]
                    break
            except Exception as e:
                print(f"  PA API Attempt {attempt} failed: {e}")
                time.sleep(2 * attempt)
                
        if not item:
            print(f"  Failed to fetch item for ASIN {asin}.")
            continue
            
        title = item.item_info.title.display_value
        safe_title = sanitize_filename(title)
        
        # Get variants
        variants = []
        if hasattr(item.images, "variants") and item.images.variants:
            variants = [v.large.url for v in item.images.variants if hasattr(v, "large") and v.large]
            
        if not variants:
            print("  No variant images found for this product.")
            continue
            
        print(f"  Found {len(variants)} variant images.")
        
        # Download up to 3 variants
        local_paths = []
        for idx, var_url in enumerate(variants[:3]):
            filename = f"{safe_title}_var{idx}.jpg"
            local_path = images_dir / filename
            rel_path = f"/markdown_images/{quote(filename)}"
            
            if not local_path.exists():
                try:
                    print(f"  Downloading variant {idx}: {filename[:40]}...")
                    download_image(var_url, local_path)
                except Exception as e:
                    print(f"    Failed to download: {e}")
                    continue
            else:
                print(f"  Variant {idx} already exists: {filename[:40]}...")
                
            local_paths.append((rel_path, f"{safe_title} variant {idx}"))
            time.sleep(0.5)
            
        if not local_paths:
            continue
            
        # Build gallery HTML
        img_tags = []
        for path, alt in local_paths:
            img_tags.append(f'<img src="{path}" alt="{alt}" style="max-width: 30%; height: auto; border-radius: 5px;" />')
            
        gallery_html = f"""
<p style="text-align: center; display: flex; justify-content: center; gap: 10px; flex-wrap: wrap; margin-top: -0.5rem; margin-bottom: 2rem;">
  {"\n  ".join(img_tags)}
</p>
""".strip()
        
        # Find where to insert this gallery.
        # We want to insert it after the Check Price button block that follows the product's main heading.
        # Find the product heading index: [Product Name](link)
        heading_match = re.search(rf'\[[^\]]+\]\({re.escape(link)}\)', updated_content)
        start_search_idx = 0
        if heading_match:
            start_search_idx = heading_match.end()
            
        # Find the button block pattern starting from start_search_idx
        # Look for the closing </div> of the check price button that contains our link
        btn_pattern = re.compile(rf'href="{re.escape(link)}"[^>]*>.*?</a>\s*</div>', re.DOTALL | re.IGNORECASE)
        btn_match = btn_pattern.search(updated_content, start_search_idx)
        
        if btn_match:
            insert_pos = btn_match.end()
            # Insert gallery
            updated_content = updated_content[:insert_pos] + "\n\n" + gallery_html + updated_content[insert_pos:]
            print(f"  Successfully inserted gallery for {link}!")
        else:
            # Fallback to search from start of document if heading search failed
            btn_match_fallback = btn_pattern.search(updated_content)
            if btn_match_fallback:
                insert_pos = btn_match_fallback.end()
                updated_content = updated_content[:insert_pos] + "\n\n" + gallery_html + updated_content[insert_pos:]
                print(f"  Successfully inserted gallery (fallback) for {link}!")
            else:
                print(f"  Could not find button block for link {link} in the content.")
                
    # Save the updated content
    post_path.write_text(updated_content, encoding="utf-8")
    print(f"\nDone! Updated: {post_path}")

if __name__ == "__main__":
    main()
