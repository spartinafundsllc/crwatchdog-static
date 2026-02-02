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

#this program is used to publish articles to the crwatchdog website.  It takes a markdown file, then 
#it uses the amazon PA API to get the images and save them to a folder.  it inserts images.
#then it converts any amazon links to geniuslinks.
#then it uploads the article to the website by placing it the src folder

# Suppress deprecation warnings from amazon_paapi
warnings.filterwarnings("ignore", category=DeprecationWarning) 

# Load environment variables from .env file (if present)
load_dotenv()

# Import GeniusLink logic from existing script
try:
    import amazon_geniuslinks
except ImportError:
    # If not in the same directory, try adding the directory to path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        import amazon_geniuslinks
    except ImportError:
        # Fallback if amazon_geniuslinks.py is missing (we might need to create it or mock it)
        print("Warning: amazon_geniuslinks module not found. GeniusLink conversion might fail.")
        amazon_geniuslinks = None

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

def process_genius_links(md_content, input_path):
    """
    Uses the logic from amazon_geniuslinks.py to convert links.
    Returns: (updated_markdown, mapping)
    mapping: dict of original_amazon_url -> new_genius_url
    """
    print(">>> Phase 1: Converting to GeniusLinks...")
    
    mapping = {}
    
    if not amazon_geniuslinks:
        print("Skipping GeniusLink conversion (module not found).")
        return md_content, mapping

    # Initialize GeniusLink client
    api_key = get_env_var("GENIUSLINK_API_KEY")
    api_secret = get_env_var("GENIUSLINK_API_SECRET")
    group_id = int(get_env_var("GENIUSLINK_GROUP_ID"))
    client = amazon_geniuslinks.GeniuslinkClient(api_key, api_secret, group_id)
    
    # Load cache if exists
    cache = {}
    cache_path = Path(".geniuslink_cache.json")
    if cache_path.exists():
        cache = amazon_geniuslinks.load_cache(cache_path)
    
    # Find URLs
    spans = list(amazon_geniuslinks.iter_urls_in_markdown(md_content))
    
    # Deduplicate spans (handle overlap between Inline and Bare regexes)
    # Filter out spans that are contained within or identical to others?
    # Actually, BARE matches the exact same range as INLINE group 3 usually.
    # Simple deduplication by (start, end)
    unique_spans = {}
    for start, end, url in spans:
        unique_spans[(start, end)] = url
    
    spans = [(start, end, url) for (start, end), url in unique_spans.items()]
    
    uniq_urls = {}
    for _, _, u in spans:
        uniq_urls[amazon_geniuslinks.strip_trailing_punct(u)] = None
        
    amazon_urls = [u for u in uniq_urls.keys() 
                  if amazon_geniuslinks.is_amazon_url(u) 
                  and not amazon_geniuslinks.is_already_genius(u)]
    
    print(f"    Found {len(amazon_urls)} Amazon URLs to convert.")
    
    for u in amazon_urls:
        if u in cache:
            mapping[u] = cache[u]
            continue
        try:
            print(f"    Converting: {u[:60]}...")
            short = client.create_shorturl(u)
            mapping[u] = short
            cache[u] = short
            time.sleep(0.1) # Rate limit politeness
        except Exception as e:
            print(f"    WARNING: Failed to convert {u}: {e}")
            mapping[u] = u
            
    # Apply replacements
    reps = []
    for start, end, raw_url in spans:
        old = amazon_geniuslinks.strip_trailing_punct(raw_url)
        new = mapping.get(old)
        if not new:
            continue
        reps.append(amazon_geniuslinks.Replacement(start, end, old, new))
        
    updated_md = amazon_geniuslinks.apply_replacements(md_content, reps)
    amazon_geniuslinks.save_cache(cache_path, cache)
    
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
    if amazon_geniuslinks:
        spans = list(amazon_geniuslinks.iter_urls_in_markdown(original_md))
        for _, _, u in spans:
            clean_u = amazon_geniuslinks.strip_trailing_punct(u)
            if amazon_geniuslinks.is_amazon_url(clean_u) and not amazon_geniuslinks.is_already_genius(clean_u):
                amazon_urls.append(clean_u)
    else:
        amazon_urls = re.findall(r"https?://(?:www\.)?amazon\.[a-z\.]+/[\w\-/]+", original_md)
            
    # 1. Fetch Images (Using Original URLs)
    products_data = fetch_images_only(amazon_urls)
    
    # 2. Convert Links to Genius
    mid_md, mapping = process_genius_links(original_md, input_path)
    
    # 3. Insert Images (Using Mapping to find new locations)
    final_md = insert_images(mid_md, products_data, mapping)
    
    # 4. Anchor links to product names
    print("\n>>> Phase 4: Anchoring Links...")
    def anchor_repl(match):
        bold_text = match.group(1)
        url = match.group(2)
        return f"[{bold_text}]({url})"
    
    anchor_pattern = re.compile(r"(\*\*.*?\*\*)\s*[—:]\s*(https://geni\.us/[a-zA-Z0-9]+)")
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
