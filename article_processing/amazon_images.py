#!/usr/bin/env python3
"""
amazon_images_into_markdown.py

What it does:
- Reads a markdown (.md) file
- Finds the first N Amazon URLs (default: 3) (amazon.*, amzn.to, a.co)
- Opens each URL in Selenium (Chrome)
- Tries to click Amazon "Continue shopping" / interstitial quickly (best-effort)
- Extracts product title + main image URL
- Downloads the image to: ./content/markdown_images/
  Filename: "<product title>.jpg" (sanitized)
- Updates the article:
  - Sets YAML front matter:
      featured_image: "/content/markdown_images/<encoded>.jpg"
      featured_image_alt: "<title> hero image"
  - Inserts a hero image at the start of the article body (right after front matter if present)
    and inserts a "Check Price / Configs" button block right beneath it linking to the FIRST Amazon URL.
  - Inserts an image block + button block after the FIRST occurrence of each of the first N Amazon links
    (button links to that specific Amazon URL)
- Writes output as: <input_stem>_images.md (does NOT overwrite original)

Install:
  pip install selenium requests

ChromeDriver:
  - Ensure Chrome is installed.
  - Ensure chromedriver is installed and on PATH, OR set CHROMEDRIVER env var to its full path.

Usage:
  python amazon_images_into_markdown.py article.md
  python amazon_images_into_markdown.py article.md --headed
  python amazon_images_into_markdown.py article.md --n 3 --sleep 1.5

Notes:
- Amazon may show captchas / block headless sessions. If you hit issues, use --headed and
  complete the interstitial quickly.
- Scraping Amazon may violate their Terms of Service. The compliant alternative is Amazon PA-API.
"""

from __future__ import annotations

import argparse
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple
from urllib.parse import urlparse, quote

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

try:
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
except Exception:
    WebDriverWait = None
    EC = None


AMAZON_HOST_RE = re.compile(
    r"(^|\.)("
    r"amazon\.[a-z.]{2,}"
    r"|amzn\.to"
    r"|a\.co"
    r")$",
    re.IGNORECASE,
)

# Match URLs in markdown (inline, autolink, bare)
MD_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\((\s*<?)(https?://[^)\s>]+)(>?\s*)\)")
MD_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
BARE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>()]+)")


@dataclass
class ProductImage:
    amazon_url: str
    title: str
    image_url: str
    local_rel_path: str  # e.g. /content/markdown_images/Foo%20Bar.jpg
    local_file_path: Path


def is_amazon_url(url: str) -> bool:
    try:
        host = urlparse(url).netloc.split("@")[-1].split(":")[0].lower()
    except Exception:
        return False
    return bool(host and AMAZON_HOST_RE.search(host))


def strip_trailing_punct(url: str) -> str:
    return url.rstrip(".,;:)]}!?")


def find_first_n_amazon_urls(md: str, n: int = 3) -> List[str]:
    candidates: List[str] = []

    for m in MD_INLINE_LINK_RE.finditer(md):
        candidates.append(m.group(3))

    for m in MD_AUTOLINK_RE.finditer(md):
        candidates.append(m.group(1))

    for m in BARE_URL_RE.finditer(md):
        candidates.append(strip_trailing_punct(m.group("url")))

    out: List[str] = []
    seen = set()
    for u in candidates:
        u = strip_trailing_punct(u)
        if u in seen:
            continue
        if is_amazon_url(u):
            out.append(u)
            seen.add(u)
        if len(out) >= n:
            break
    return out


def sanitize_filename(title: str, max_len: int = 120) -> str:
    t = unicodedata.normalize("NFKD", title)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^\w\s\-.,()&+]", "", t, flags=re.UNICODE).strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        t = "amazon_product"
    if len(t) > max_len:
        t = t[:max_len].rstrip()
    return t


def build_chrome(headed: bool) -> webdriver.Chrome:
    opts = Options()
    if not headed:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1400,1000")

    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    user_agent = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_argument(f"--user-agent={user_agent}")

    chromedriver = os.getenv("CHROMEDRIVER")
    if chromedriver:
        service = webdriver.chrome.service.Service(chromedriver)
        driver = webdriver.Chrome(service=service, options=opts)
    else:
        driver = webdriver.Chrome(options=opts)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
        )
    except Exception:
        pass

    return driver


def try_click_continue_shopping(driver: webdriver.Chrome, timeout_s: float = 1.0) -> None:
    if WebDriverWait is None or EC is None:
        return

    xpaths = [
        "//button[contains(.,'Continue shopping') or contains(.,'Continue Shopping') or contains(.,'Continue')]",
        "//a[contains(.,'Continue shopping') or contains(.,'Continue Shopping') or contains(.,'Continue')]",
        "//input[@type='submit' and (contains(@value,'Continue') or contains(@aria-label,'Continue'))]",
    ]
    for xp in xpaths:
        try:
            el = WebDriverWait(driver, timeout_s).until(EC.element_to_be_clickable((By.XPATH, xp)))
            el.click()
            return
        except Exception:
            continue


def extract_title_and_image(driver: webdriver.Chrome) -> Tuple[str, str]:
    title = ""
    try:
        title = driver.find_element(By.ID, "productTitle").text.strip()
    except Exception:
        pass

    if not title:
        title = (driver.title or "").strip()
        title = re.sub(r"\s*:\s*Amazon\..*$", "", title).strip() or "amazon_product"

    image_url = ""
    try:
        og = driver.find_element(By.CSS_SELECTOR, 'meta[property="og:image"]')
        image_url = (og.get_attribute("content") or "").strip()
    except Exception:
        pass

    if not image_url:
        try:
            img = driver.find_element(By.ID, "landingImage")
            image_url = (img.get_attribute("data-old-hires") or "").strip() or (img.get_attribute("src") or "").strip()
        except Exception:
            pass

    if not image_url:
        raise RuntimeError("Could not find product image URL (blocked/captcha/layout changed).")

    return title, image_url


def download_image_as_jpg(image_url: str, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Referer": "https://www.amazon.com/",
    }
    r = requests.get(image_url, headers=headers, timeout=30)
    r.raise_for_status()
    out_file.write_bytes(r.content)


def url_to_safe_relpath(filename: str) -> str:
    return "/content/markdown_images/" + quote(filename)


def build_check_price_block(url: str, label: str = "Check Price / Configs") -> str:
    # Matches your styling example (Amazon URL is used as href)
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
    {label}
  </a>
</div>

""".lstrip("\n")


def upsert_featured_image_front_matter(md: str, featured_path: str, featured_alt: str) -> str:
    def upsert_line(fm: str, key: str, value: str) -> str:
        pattern = re.compile(rf"(?m)^{re.escape(key)}\s*:\s*.*$")
        new_line = f'{key}: "{value}"'
        if pattern.search(fm):
            return pattern.sub(new_line, fm)
        if not fm.endswith("\n"):
            fm += "\n"
        return fm + new_line + "\n"

    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            fm = md[4:end+1]
            body = md[end + len("\n---\n"):]
            fm2 = upsert_line(fm, "featured_image", featured_path)
            fm2 = upsert_line(fm2, "featured_image_alt", featured_alt)
            return "---\n" + fm2 + "---\n" + body

    fm2 = f'featured_image: "{featured_path}"\nfeatured_image_alt: "{featured_alt}"\n'
    return "---\n" + fm2 + "---\n\n" + md


def insert_hero_image_and_button(md: str, rel_path: str, alt: str, button_url: str) -> str:
    image_line = f"![{alt}]({rel_path})\n\n"
    button = build_check_price_block(button_url)
    insert_blob = image_line + button

    if md.startswith("---\n"):
        end = md.find("\n---\n", 4)
        if end != -1:
            insert_at = end + len("\n---\n")
            return md[:insert_at] + "\n" + insert_blob + md[insert_at:]
    return insert_blob + md


def insert_image_and_button_after_first_link(md: str, amazon_url: str, rel_path: str, alt: str) -> str:
    img_block = (
        "\n\n<p>\n"
        f'  <img\n'
        f'    src="{rel_path}"\n'
        f'    alt="{alt}"\n'
        f'    style="max-width: 92%; height: auto; display: block; margin: 1rem auto; border-radius: 10px;"\n'
        f'  />\n'
        "</p>\n\n"
    )
    button = build_check_price_block(amazon_url)
    insert_blob = img_block + button

    idx = md.find(amazon_url)
    if idx == -1:
        idx = md.find(strip_trailing_punct(amazon_url))
        if idx == -1:
            return md

    insert_pos = idx + len(amazon_url)
    return md[:insert_pos] + insert_blob + md[insert_pos:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_md", type=Path, help="Input markdown file")
    ap.add_argument("--n", type=int, default=3, help="How many Amazon links to process (default: 3)")
    ap.add_argument("--headed", action="store_true", help="Run Chrome with a visible window")
    ap.add_argument("--sleep", type=float, default=1.5, help="Seconds to wait after page load (default: 1.5)")
    ap.add_argument("--click-timeout", type=float, default=1.0, help='Seconds to wait for "Continue shopping" click (default: 1.0)')
    args = ap.parse_args()

    if not args.input_md.exists():
        print(f"ERROR: file not found: {args.input_md}", file=os.sys.stderr)
        return 2

    md = args.input_md.read_text(encoding="utf-8")
    amazon_urls = find_first_n_amazon_urls(md, n=args.n)

    if not amazon_urls:
        print("No Amazon URLs found in the markdown.", file=os.sys.stderr)
        return 1

    images_dir = Path("content") / "markdown_images"
    images_dir.mkdir(parents=True, exist_ok=True)

    driver = build_chrome(headed=args.headed)
    products: List[ProductImage] = []
    try:
        for u in amazon_urls:
            driver.get(u)
            try_click_continue_shopping(driver, timeout_s=args.click_timeout)
            time.sleep(args.sleep)

            title, image_url = extract_title_and_image(driver)
            safe_title = sanitize_filename(title)
            filename = f"{safe_title}.jpg"
            out_file = images_dir / filename

            download_image_as_jpg(image_url, out_file)
            rel_path = url_to_safe_relpath(filename)

            products.append(ProductImage(
                amazon_url=u,
                title=safe_title,
                image_url=image_url,
                local_rel_path=rel_path,
                local_file_path=out_file,
            ))

            print(f"Saved: {out_file}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    featured = products[0]
    featured_alt = f"{featured.title} hero image"

    md2 = upsert_featured_image_front_matter(md, featured.local_rel_path, featured_alt)
    md2 = insert_hero_image_and_button(md2, featured.local_rel_path, featured.title, button_url=featured.amazon_url)

    for p in products:
        md2 = insert_image_and_button_after_first_link(md2, p.amazon_url, p.local_rel_path, alt=p.title)

    out_path = args.input_md.with_name(f"{args.input_md.stem}_images{args.input_md.suffix}")
    out_path.write_text(md2, encoding="utf-8")
    print(f"\nWrote updated markdown: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
