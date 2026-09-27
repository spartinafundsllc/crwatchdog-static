import argparse
import os
import sys
import time
import re
import requests
import warnings
from pathlib import Path
from urllib.parse import urlparse, quote
from amazon_paapi import AmazonApi
from dotenv import load_dotenv

# This program is used to publish articles to the CRWatchdog website.
# It takes a markdown file, fetches product images via Amazon Creators API / catalog,
# saves them to src/markdown_images/, canonicalizes all Amazon links to direct
# affiliate URLs (tag=crwatchdog-20) supported by Amazon OneLink for international redirection,
# inserts product image blocks and "Check Price" buttons, and outputs to src/posts/.
#
# NOTE: Legacy PAAPI v5 is deprecated by Amazon; future catalog lookups must use
# the new Amazon Creators API.
# Geniuslink has been retired and is no longer used.

# Suppress deprecation warnings from amazon_paapi
warnings.filterwarnings("ignore", category=DeprecationWarning) 

# Load environment variables from .env file (if present)
load_dotenv()


# --- Constants & Helpers ---

def get_env_var(name, required=True):
    val = os.getenv(name)
    if required and not val:
        print(f"ERROR: Missing environment variable: {name}", file=sys.stderr)
        sys.exit(1)
    return val

def extract_asin(url):
    """Refined ASIN extraction from various Amazon URL formats."""
    # Matches /dp/B0..., /gp/product/B0..., /B0...
    match = re.search(r"(?:/dp/|/gp/product/|/)([B0-9][A-Z0-9]{9})(?:/|\?|$)", url)
    if match:
        return match.group(1)
    return None

def sanitize_filename(title, max_len=120):
    """Sanitizes product title for use as a filename."""
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
    """Downloads an image from a URL to a local path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "CRWatchdog-Publisher/1.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    path.write_bytes(r.content)

def build_check_price_block(url):
    """Generates the HTML for the 'Check Price' button (CRWatchdog Style - Blue)."""
    # Using #2a68c4 based on site footer/branding
    return f"""
<div style="text-align:center; margin: 1.75rem 0;">
  <a
    href="{url}"
    target="_blank"
    rel="sponsored nofollow noopener"
    style="
      display: inline-block;
      background-color: #2a68c4;
      color: #fff;
      padding: 14px 26px;
      border-radius: 6px;
      font-weight: 700;
      font-size: 1.2rem;
      letter-spacing: 0.4px;
      text-decoration: none;
      text-transform: uppercase;
      box-shadow: 0 4px 6px rgba(0,0,0,0.1);
      transition: background-color 0.15s ease, transform 0.1s ease;
    "
    onmouseover="this.style.backgroundColor='#1d4e9e'; this.style.transform='translateY(-1px)';"
    onmouseout="this.style.backgroundColor='#2a68c4'; this.style.transform='translateY(0)';"
  >
    Check Price on Amazon
  </a>
</div>
""".strip()

# --- Main Logic ---

def canonicalize_amazon_links(md_content, input_path):
    """
    Directly canonicalizes all Amazon product and search URLs to direct Amazon
    affiliate URLs using the active AMAZON_TAG. International geo-redirection is
    handled automatically by Amazon OneLink without third-party redirects.
    Returns: (updated_markdown, mapping)
    mapping: dict of original_url -> canonical_amazon_url
    """
    print(">>> Phase 2: Canonicalizing Amazon URLs to direct affiliate links (OneLink)...")
    tag = get_env_var("AMAZON_TAG")
    mapping = {}

    AMAZON_FIND_RE = re.compile(r'https?://(?:[a-zA-Z0-9-]+\.)?amazon\.[a-z\.]+(?:/[^\s\)\"\'>]*)?', re.IGNORECASE)

    def clean_punct(u):
        return u.rstrip(".,;:!\"'“”’)\\]")

    def canonicalize_single(raw_u):
        u = clean_punct(raw_u)
        asin = extract_asin(u)
        if asin:
            return f"https://www.amazon.com/dp/{asin}?tag={tag}"

        parsed = urlparse(u)
        if parsed.path.startswith("/s") or "k=" in u or "field-keywords=" in u or "keywords=" in u:
            from urllib.parse import parse_qs, unquote, quote_plus
            qs = parse_qs(parsed.query)
            kw = qs.get("k", qs.get("field-keywords", qs.get("keywords", [])))
            if kw and kw[0]:
                k_val = unquote(kw[0]).strip()
                return f"https://www.amazon.com/s?k={quote_plus(k_val)}&tag={tag}"
            return f"https://www.amazon.com/s?tag={tag}"

        if "?" in u:
            from urllib.parse import parse_qs, quote_plus
            qs = parse_qs(parsed.query)
            qs["tag"] = [tag]
            qs.pop("geniuslink", None)
            qs.pop("th", None)
            qs.pop("psc", None)
            clean_query = "&".join(f"{k}={quote_plus(v)}" for k, vals in qs.items() for v in vals)
            return f"https://www.amazon.com{parsed.path}?{clean_query}"
        return f"{u}?tag={tag}"

    matches = AMAZON_FIND_RE.findall(md_content)
    for raw in matches:
        clean = clean_punct(raw)
        if clean not in mapping:
            canonical = canonicalize_single(clean)
            mapping[clean] = canonical
            mapping[raw] = canonical + raw[len(clean):]

    updated_md = md_content
    for old_url in sorted(mapping.keys(), key=lambda k: len(k), reverse=True):
        updated_md = updated_md.replace(old_url, mapping[old_url])

    print(f"    Canonicalized {len(mapping)} Amazon URLs with tag={tag}.")
    return updated_md, mapping

def fetch_images_only(amazon_urls):
    """
    Fetches images for the given Amazon URLs.
    Returns: list of products_data
    """
    print("\n>>> Phase 2: Fetching Images via Amazon API...")
    
    access_key = get_env_var("AMAZON_ACCESS_KEY")
    secret_key = get_env_var("AMAZON_SECRET_KEY")
    tag = get_env_var("AMAZON_TAG")
    region = os.getenv("AMAZON_REGION", "US")
    
    try:
        amazon = AmazonApi(access_key, secret_key, tag, region)
    except Exception as e:
        print(f"    Failed to initialize Amazon API: {e}")
        return []
    
    # Limit to first 20 distinct products
    seen = set()
    target_urls = []
    for u in amazon_urls:
         if u not in seen:
             target_urls.append(u)
             seen.add(u)
         if len(target_urls) >= 20: 
             break

    if not target_urls:
        print("    No Amazon URLs found to fetch images for.")
        return []

    # Locate Project Root
    project_root = Path(os.getcwd()).resolve()
    while not (project_root / ".eleventy.js").exists() and not (project_root / "package.json").exists():
        if project_root.parent == project_root:
            print("Warning: Could not locate project root. Using current directory.")
            project_root = Path(os.getcwd()).resolve()
            break
        project_root = project_root.parent
    
    # Destination for images
    if (project_root / "src/markdown_images").exists():
        images_dir_abs = project_root / "src/markdown_images"
    else:
        images_dir_abs = project_root / "src" / "markdown_images" 
    
    images_dir_abs.mkdir(parents=True, exist_ok=True)
    
    products_data = [] 

    for i, url in enumerate(target_urls):
        if i > 0:
             print("    Pausing 2s...")
             time.sleep(2)

        asin = extract_asin(url)
        if not asin:
            print(f"    Skipping URL (No ASIN found): {url}")
            continue
            
        print(f"    Fetching ASIN: {asin}...")
        
        # Retry Logic
        item = None
        for attempt in range(1, 4):
            try:
                items = amazon.get_items(asin)
                if items:
                    item = items[0]
                    break
                else:
                    break
            except Exception as e:
                if "limit reached" in str(e).lower() or "throttling" in str(e).lower():
                     wait_time = 2 * (2 ** attempt)
                     time.sleep(wait_time)
                else:
                     break
        
        if not item:
            print(f"    Could not fetch item for ASIN {asin}.")
            continue
            
        title = item.item_info.title.display_value
        image_url = None
        if item.images and item.images.primary and item.images.primary.large:
            image_url = item.images.primary.large.url
            
        if not image_url:
            print(f"    No large image found for {title[:30]}...")
            continue
                
        safe_title = sanitize_filename(title)
        filename = f"{safe_title}.jpg"
        local_path = images_dir_abs / filename
        rel_path = f"/markdown_images/{quote(filename)}"
            
        if not local_path.exists():
            try:
                download_image(image_url, local_path)
                print(f"    Downloaded: {filename}")
            except Exception as e:
                print(f"    Failed: {e}")
                continue
        else:
            print(f"    Exists: {filename}")
                
        products_data.append({
            "title": safe_title,
            "rel_path": rel_path,
            "anchor_url": url, 
            "insert_type": "inline"
        })
            
    return products_data

def insert_images(md_content, products_data, mapping):
    """
    Inserts image blocks into the markdown.
    Uses 'mapping' to resolve original Amazon URLs to the NEW Genius links present in md_content.
    """
    print("\n>>> Phase 3: Inserting Images into Markdown...")
    final_md = md_content
    
    if not products_data:
        return final_md

    # 1. Handle Hero (First product)
    hero = products_data[0]
    
    # Update Front Matter
    if final_md.startswith("---"):
        fm_end = final_md.find("\n---", 3)
        if fm_end != -1:
            front_matter = final_md[3:fm_end]
            front_matter = re.sub(r"\nfeatured_image:.*", "", front_matter)
            front_matter = re.sub(r"\nfeatured_image_alt:.*", "", front_matter)
            new_lines = f"\nfeatured_image: \"{hero['rel_path']}\"\nfeatured_image_alt: \"{hero['title']} hero image\""
            final_md = "---" + front_matter + new_lines + final_md[fm_end:]

    # 2. Insert Hero Block after Front Matter
    fm_end_pattern = re.compile(r"^---\s*$", re.MULTILINE)
    matches = list(fm_end_pattern.finditer(final_md))
    body_start_idx = matches[1].end() if len(matches) >= 2 else 0
    
    # Helper to resolve URL
    def resolve_url(orig_url):
        return mapping.get(orig_url, orig_url)

    hero_url = resolve_url(hero['anchor_url'])
    
    hero_block = f"""
<p style="text-align: center;">
  <img 
    src="{hero['rel_path']}" 
    alt="{hero['title']} hero image"
    style="max-width: 100%; height: auto; border-radius: 10px;"
  />
</p>
"""
    hero_block += build_check_price_block(hero_url) + "\n\n"
    
    final_md = final_md[:body_start_idx] + "\n" + hero_block + final_md[body_start_idx:]
    
    # 2.5 Remove Redundant Title
    title_match = re.search(r"title: \"(.*?)\"", final_md[:body_start_idx])
    if title_match:
        page_title = title_match.group(1)
        h1_pattern = re.compile(rf"^# {re.escape(page_title)}\s*$", re.MULTILINE)
        final_md = h1_pattern.sub("", final_md)
        h1_pattern_simple = re.compile(rf"^# [“\"]{re.escape(page_title)}[”\"]\s*$", re.MULTILINE)
        final_md = h1_pattern_simple.sub("", final_md)
    
    # 3. Insert blocks for ALL products
    for p in products_data:
        # Resolve to the NEW link
        target_link = resolve_url(p['anchor_url'])
        
        block = f"""
<p>
  <img
    src="{p['rel_path']}"
    alt="{p['title']}"
    style="max-width: 92%; height: auto; display: block; margin: 1rem auto; border-radius: 10px;"
  />
</p>
"""
        block += build_check_price_block(target_link) + "\n\n"
        
        # Search for the target link (Genius link if converted)
        idx = final_md.find(target_link, body_start_idx + len(hero_block)) 
        
        if idx != -1:
            insert_pos = idx + len(target_link)
            # Find end of link
            lookahead = final_md[insert_pos:insert_pos+5]
            if lookahead.startswith(")"):
                insert_pos += 1
            elif lookahead.startswith('">'): 
                 end_a = final_md.find("</a>", insert_pos)
                 if end_a != -1:
                     insert_pos = end_a + 4
            
            final_md = final_md[:insert_pos] + "\n\n" + block + final_md[insert_pos:]
            
    return final_md


def main():
    parser = argparse.ArgumentParser(description="Publish CRWatchdog Article")
    parser.add_argument("input", type=str, help="Input Markdown file name")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: {input_path} not found.")
        sys.exit(1)

    original_md = input_path.read_text(encoding="utf-8")
    
    # 0. Find Amazon URLs (Original)
    amazon_urls = []
    raw_amazon_matches = re.findall(r"https?://(?:[a-zA-Z0-9-]+\.)?amazon\.[a-z\.]+/[\w\-/]+", original_md)
    for u in raw_amazon_matches:
        clean_u = u.rstrip(".,;:!\"'“”’)\\]")
        if clean_u not in amazon_urls:
            amazon_urls.append(clean_u)
            
    # 1. Fetch Images (Using Original URLs)
    products_data = fetch_images_only(amazon_urls)
    
    # 2. Canonicalize Amazon URLs to Direct Affiliate Links
    mid_md, mapping = canonicalize_amazon_links(original_md, input_path)
    
    # 3. Insert Images (Using Mapping to find new locations)
    final_md = insert_images(mid_md, products_data, mapping)
    
    # 4. Anchor links to product names
    print("\n>>> Phase 4: Anchoring Links...")
    def anchor_repl(match):
        bold_text = match.group(1)
        url = match.group(2)
        return f"[{bold_text}]({url})"
    
    anchor_pattern = re.compile(r"(\*\*.*?\*\*)\s*[—:]\s*(https?://(?:www\.)?amazon\.[a-z\.]+[^\s\)]+)")
    final_md = anchor_pattern.sub(anchor_repl, final_md)

    # 5. Output
    slug = input_path.stem
    slug = re.sub(r"-v\d+$", "", slug)
    output_filename = f"{slug}.md"
    
    project_root = Path(os.getcwd()).resolve()
    while not (project_root / ".eleventy.js").exists() and not (project_root / "package.json").exists():
        if project_root.parent == project_root:
            project_root = Path(os.getcwd()).resolve()
            break
        project_root = project_root.parent

    output_dir = project_root / "src" / "posts"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_filename
    
    output_path.write_text(final_md, encoding="utf-8")
    
    print(f"\nSUCCESS! Created: {output_path}")
    print(f"Images downloaded: {len(products_data)}")

if __name__ == "__main__":
    main()
