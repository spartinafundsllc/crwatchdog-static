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
    import amazon_geniuslinks

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
    headers = {"User-Agent": "MonitorNerds-Publisher/1.0"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    path.write_bytes(r.content)

def build_check_price_block(url):
    """Generates the HTML for the 'Check Price / Configs' button."""
    return f"""
<div style="text-align:center; margin: 1.75rem 0;">
  <a
    href="{url}"
    target="_blank"
    rel="sponsored nofollow noopener"
    style="
      display: inline-block;
      background-color: #f7ca00;
      color: #000;
      padding: 14px 26px;
      border-radius: 6px;
      font-weight: 700;
      font-size: 1.2rem;
      letter-spacing: 0.4px;
      text-decoration: none;
      text-transform: uppercase;
      box-shadow: 0 1px 0 rgba(0,0,0,0.15);
      transition: background-color 0.15s ease, transform 0.1s ease;
    "
    onmouseover="this.style.backgroundColor='#e6b800'; this.style.transform='translateY(-1px)';"
    onmouseout="this.style.backgroundColor='#f7ca00'; this.style.transform='translateY(0)';"
  >
    Check Price / Configs
  </a>
</div>
""".strip()

def build_author_bio_block():
    """Generates the HTML for the 'About the Author' section."""
    return """
---

## About the Author

<div style="display: flex; align-items: flex-start; gap: 20px; margin-top: 2rem; background: #f9f9f9; padding: 20px; border-radius: 10px;">
  <img 
    src="/content/markdown_images/paolo-reva.jpg" 
    alt="Paolo Reva" 
    style="width: 100px; height: 100px; border-radius: 50%; object-fit: cover; flex-shrink: 0;"
  />
  <div>
    <h3 style="margin-top: 0;">Paolo Reva</h3>
    <p style="margin-bottom: 0.5rem;"><a href="mailto:paolo@monitornerds.com">paolo@monitornerds.com</a></p>
    <p style="font-size: 1.05rem; line-height: 1.6;">
      Paolo is a gaming veteran since the golden days of Doom and Warcraft and has been building gaming systems for family, friends, and colleagues since his junior high years. High-performance monitors are one of his fixations and he believes that it’s every citizen’s right to enjoy one. He has gone through several pieces of hardware in pursuit of every bit of performance gain, much to the dismay of his wallet. He now works with Monitornerds to scrutinize the latest gear to create reviews which accentuate the seldom explained aspects of a PC monitor.
    </p>
  </div>
</div>
""".strip()

# --- Main Logic ---

def process_genius_links(md_content, input_path):
    """
    Uses the logic from amazon_geniuslinks.py to convert links.
    Returns: (updated_markdown, amazon_urls_map)
    amazon_urls_map: dict of original_amazon_url -> new_genius_url
    """
    print(">>> Phase 1: Converting to GeniusLinks...")
    
    # Initialize GeniusLink client
    api_key = get_env_var("GENIUSLINK_API_KEY")
    api_secret = get_env_var("GENIUSLINK_API_SECRET")
    group_id = int(get_env_var("GENIUSLINK_GROUP_ID"))
    client = amazon_geniuslinks.GeniuslinkClient(api_key, api_secret, group_id)
    
    # Load cache if exists
    cache_path = Path(".geniuslink_cache.json")
    cache = amazon_geniuslinks.load_cache(cache_path)
    
    # Find URLs
    spans = list(amazon_geniuslinks.iter_urls_in_markdown(md_content))
    uniq_urls = {}
    for _, _, u in spans:
        uniq_urls[amazon_geniuslinks.strip_trailing_punct(u)] = None
        
    amazon_urls = [u for u in uniq_urls.keys() 
                  if amazon_geniuslinks.is_amazon_url(u) 
                  and not amazon_geniuslinks.is_already_genius(u)]
    
    print(f"    Found {len(amazon_urls)} Amazon URLs to convert.")
    
    mapping = {}
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
    
    # We return the mapping so we know which original URLs correlate to products we might fetch images for
    return updated_md, amazon_urls

def process_images_before_conversion(md_content, amazon_urls):
    """
    Fetches images and inserts them into markdown. 
    Returns: (updated_markdown, amazon_urls_map)
    """
    print("\n>>> Phase 2: Fetching Images via Amazon API...")
    
    access_key = get_env_var("AMAZON_ACCESS_KEY")
    secret_key = get_env_var("AMAZON_SECRET_KEY")
    tag = get_env_var("AMAZON_TAG")
    region = os.getenv("AMAZON_REGION", "US")
    
    amazon = AmazonApi(access_key, secret_key, tag, region)
    
    # Limit to first 20 distinct products (increased from 3)
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
        return md_content, []

    images_dir = Path("content/markdown_images")
    images_dir.mkdir(parents=True, exist_ok=True)
    
    products_data = [] 

    for i, url in enumerate(target_urls):
        # Throttle between requests (start after first one)
        if i > 0:
             print("    Pausing 5s to avoid throttling...")
             time.sleep(5)

        asin = extract_asin(url)
        if not asin:
            print(f"    Skipping URL (No ASIN found): {url}")
            continue
            
        print(f"    Fetching ASIN: {asin}...")
        
        # Retry Logic
        # Try up to 3 times with exponential backoff
        item = None
        for attempt in range(1, 4):
            try:
                items = amazon.get_items(asin)
                if items:
                    item = items[0]
                    break
                else:
                    # Item not found, don't retry
                    break
            except Exception as e:
                if "limit reached" in str(e).lower() or "throttling" in str(e).lower():
                     wait_time = 5 * (2 ** attempt)
                     print(f"    Throttled. Retrying in {wait_time}s...")
                     time.sleep(wait_time)
                else:
                     print(f"    API Error for {asin}: {e}")
                     break
        
        if not item:
            print(f"    Could not fetch item for ASIN {asin} after retries.")
            continue
            
        title = item.item_info.title.display_value
            
        # Find image
        image_url = None
        if item.images and item.images.primary and item.images.primary.large:
            image_url = item.images.primary.large.url
            
        if not image_url:
            print(f"    No large image found for {title[:30]}...")
            continue
                
        safe_title = sanitize_filename(title)
        filename = f"{safe_title}.jpg"
        local_path = images_dir / filename
        rel_path = f"/content/markdown_images/{quote(filename)}"
            
        if not local_path.exists():
            download_image(image_url, local_path)
            print(f"    Downloaded: {filename}")
        else:
            print(f"    Exists: {filename}")
                
        products_data.append({
            "title": safe_title,
            "rel_path": rel_path,
            "anchor_url": url, 
            "insert_type": "inline"
        })
            
    if not products_data:
        return md_content, []
        
    print("\n>>> Phase 3: Inserting Images into Markdown...")
    final_md = md_content
    
    # Mark the products for insertion
    # We need to insert them relative to their links.
    # Since we are modifying the string, we should do it from bottom up or carefully.
    
    # Strategy: Find all positions of all target URLs.
    # We only insert an image block ONCE per product, ideally after its first mention (or as hero if it's the first one).
    
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
    
    # Extract Title for Byline
    import datetime
    pub_date = datetime.date.today().strftime("%B %-d, %Y")
    # Try to extract date from front matter if available
    fm_date_match = re.search(r"date: (\d{4}-\d{2}-\d{2})", final_md[:body_start_idx])
    if fm_date_match:
        try:
            pub_date = datetime.datetime.strptime(fm_date_match.group(1), "%Y-%m-%d").strftime("%B %-d, %Y")
        except:
            pass

    hero_block = f"""
<p style="text-align: center;">
  <img 
    src="{hero['rel_path']}" 
    alt="{hero['title']} hero image"
    style="max-width: 100%; height: auto; border-radius: 10px;"
  />
</p>

<p style="text-align: center; font-style: italic; color: #666; margin-top: -1rem;">
  By <strong>Paolo Reva</strong> | Published {pub_date}
</p>
"""
    hero_block += build_check_price_block(hero['anchor_url']) + "\n\n"
    
    final_md = final_md[:body_start_idx] + "\n" + hero_block + final_md[body_start_idx:]
    
    # 2.5 Remove Redundant Title
    # Find the title in front matter
    title_match = re.search(r"title: \"(.*?)\"", final_md[:body_start_idx])
    if title_match:
        page_title = title_match.group(1)
        # Search for this title as an H1 in the body
        h1_pattern = re.compile(rf"^# {re.escape(page_title)}\s*$", re.MULTILINE)
        final_md = h1_pattern.sub("", final_md)
        # Also check for alternate quotes or simple matches
        h1_pattern_simple = re.compile(rf"^# [“\"]{re.escape(page_title)}[”\"]\s*$", re.MULTILINE)
        final_md = h1_pattern_simple.sub("", final_md)
    
    # 3. Insert blocks for ALL products (including the hero if we want it in body too? 
    # Usually hero is enough for first one. standard logic: "Inserts... after the FIRST occurrence")
    # Let's insert for ALL of them. 
    
    # We need to recalculate positions because final_md changed.
    # It's safer to split the text or use a robust replacement.
    # Let's use string replacement which finds the URL and appends the block?
    # BUT we only want it after the FIRST occurrence in the text (excluding front matter?)
    
    # Simple approach: Iterate products, find first occurrence of anchor_url, insert block after it.
    # We must do this carefully so we don't mess up subsequent insertions.
    # Using a placeholder might be safer, but direct insertion is okay if we re-find.
    
    for p in products_data:
        # Construct block
        block = f"""
<p>
  <img
    src="{p['rel_path']}"
    alt="{p['title']}"
    style="max-width: 92%; height: auto; display: block; margin: 1rem auto; border-radius: 10px;"
  />
</p>
"""
        block += build_check_price_block(p['anchor_url']) + "\n\n"
        
        # Find the link
        # Note: The link might be `[Look here](https://amazon...)` or `<https://amazon...>`
        # We search for the URL string.
        idx = final_md.find(p['anchor_url'], body_start_idx + len(hero_block)) 
        # Start search AFTER hero block to avoid matching the hero button's link 
        # (Wait, hero button uses the same link! We don't want to insert right after the hero button and duplicate it immediately)
        
        if idx != -1:
            # We found the URL. Insert AFTER the URL.
            insert_pos = idx + len(p['anchor_url'])
            # Check if we are inside a markdown link parentheses `](...)`
            # If so, we probably want to insert after the closing `)`
            # This is getting complicated parsing markdown with regex.
            # Simplified: Just append after the URL. Markdown handles HTML blocks fine usually.
            
            # To be safer: Search for the closing parenthesis of the link if it looks like a markdown link
            lookahead = final_md[insert_pos:insert_pos+5]
            if lookahead.startswith(")"):
                insert_pos += 1
            elif lookahead.startswith('">'): # HTML link end
                 # find end of a tag
                 end_a = final_md.find("</a>", insert_pos)
                 if end_a != -1:
                     insert_pos = end_a + 4
            
            final_md = final_md[:insert_pos] + "\n\n" + block + final_md[insert_pos:]
            
    return final_md, products_data


def main():
    parser = argparse.ArgumentParser(description="Publish MonitorNerds Article")
    parser.add_argument("input", type=Path, help="Input Markdown file")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found.")
        sys.exit(1)

    original_md = args.input.read_text(encoding="utf-8")
    
    # 0. Find Amazon URLs first (using reliable regex from geniuslink script)
    # We need these to fetch images BEFORE we convert them to Genius links.
    spans = list(amazon_geniuslinks.iter_urls_in_markdown(original_md))
    amazon_urls = []
    for _, _, u in spans:
        clean_u = amazon_geniuslinks.strip_trailing_punct(u)
        if amazon_geniuslinks.is_amazon_url(clean_u) and not amazon_geniuslinks.is_already_genius(clean_u):
            amazon_urls.append(clean_u)
            
    # 1. Fetch Images & Modify Markdown to include them
    md_with_images, products = process_images_before_conversion(original_md, amazon_urls)
    
    # 2. Convert Links to Genius
    mid_md, _ = process_genius_links(md_with_images, args.input)
    
    # 2.5 Anchor links to product names and add Bio
    print("\n>>> Phase 4: Anchoring Links & Adding Bio...")
    
    # Replace "**Product Name** — https://geni.us/..." with "[**Product Name**](https://geni.us/...)"
    def anchor_repl(match):
        bold_text = match.group(1)
        url = match.group(2)
        return f"[{bold_text}]({url})"
    
    # Pattern matches "**text** — http..." or "**text**: http..."
    anchor_pattern = re.compile(r"(\*\*.*?\*\*)\s*[—:]\s*(https://geni\.us/[a-zA-Z0-9]+)")
    final_md = anchor_pattern.sub(anchor_repl, mid_md)

    # Also handle some text-based replacements at end of article
    final_md = re.sub(r"start with the (.*?): (https://geni\.us/[a-zA-Z0-9]+)", r"start with the [\1](\2)", final_md)
    final_md = re.sub(r"recommendation: (https://geni\.us/[a-zA-Z0-9]+)", r"recommendation: [\1](\2)", final_md) # Missed the (AOC Q27...) case but let's be broad
    
    # Manual final fixes for common patterns
    final_md = final_md.replace("is on Amazon here: https://geni.us/", "is on Amazon here: [https://geni.us/") # Hmm this is tricky
    # Let's use a simpler approach for the verdict section links
    final_md = re.sub(r"([a-zA-Z0-9 ]+): (https://geni\.us/[a-zA-Z0-9]+)\.", r"[\1](\2).", final_md)

    # Append Bio
    if "About the Author" not in final_md:
        final_md = final_md.rstrip() + "\n\n" + build_author_bio_block() + "\n"
    
    # 3. Write Output
    output_path = args.input.with_name(f"{args.input.stem}_ready.md")
    output_path.write_text(final_md, encoding="utf-8")
    
    print(f"\nSUCCESS! Created: {output_path}")
    print(f"Images downloaded: {len(products)}")

if __name__ == "__main__":
    main()
