# CRWatchdog Agent Context & Developer Handbook

This document serves as the single source of truth for architecture, post scheduling, publication pipelines, editorial guidelines, and styling standards for **CRWatchdog**.

---

## 1. Project Overview & Technology Stack

* **Architecture**: 100% static site generated via **Eleventy (11ty v2.0.1)** and hosted on **Netlify CDN**.
* **Zero Backend / Serverless**: No PHP, MySQL, or Node backend is running at runtime. The output directory is `_site/`.
* **Templates**: Nunjucks (`.njk`) layouts and Markdown (`.md`) articles.
* **Content Automation**: Python 3 pipeline in `article_processing/` using the Amazon PA API, GeniusLink API, and local caching.
* **Styling**: Vanilla CSS with legacy Genesis/WordPress styling complemented by modern overrides in `static/css/11ty-overrides.css`.

---

## 2. Directory Structure

```
crwatchdog-static/
├── .eleventy.js             # 11ty config: collections, passthrough, filters, visibility logic
├── _redirects               # Netlify 301/302 redirects
├── package.json             # NPM dependencies (@11ty/eleventy, luxon)
├── src/
│   ├── _data/               # Global site data
│   ├── _includes/
│   │   ├── layouts/         # Layouts (base.njk, post.njk, etc.)
│   │   └── partials/        # Reusable template snippets
│   ├── markdown_images/     # Dedicated storage for product & article images
│   ├── posts/               # Published Markdown articles (site content)
│   ├── appliances.njk       # Appliances Hub landing page (/category/appliances/)
│   └── index.njk            # Homepage template with custom featured sections
├── static/                  # Static assets & legacy HTML folders served at root
├── article_processing/      # Content publishing pipeline & Python scripts
│   ├── .env                 # API credentials (AMAZON, GENIUSLINK)
│   ├── .geniuslink_cache.json # Cached affiliate link mappings
│   ├── publish_article_crwatchdog.py # Main article publishing script
│   └── amazon_images.py     # Image acquisition utilities
└── agent_context.md         # This single source of truth document
```

---

## 3. Post Scheduling & Visibility Logic (`.eleventy.js`)

The site has custom filtering rules to distinguish local previewing from live production deployments:

### Environment Rules
* **Local Development (`npm run dev`)**:
  * All posts are rendered, including drafts (`draft: true`) and future-dated articles (`date > NOW`), enabling seamless content previewing.
* **Production (`ELEVENTY_ENV=production npm run build` or Netlify)**:
  * Drafts (`draft: true`) are **excluded**.
  * Future-dated posts (`date > NOW`) are **excluded**.
  * Handled by the `isAllowed(item)` helper in `.eleventy.js`.

### Collections (`index.njk`)
* **`homepageLatest`**: Returns newest-first articles. Automatically excludes:
  * Posts tagged/categorized as Cars.
  * The homepage anchor hero post (`HOMEPAGE_ANCHOR_URL = "/best-tvs-of-2026-according-to-consumer-reports/"` or `homepage_anchor: true`) to avoid duplicate card rendering.
* **`latestCars`**: Returns articles tagged with `tags: ["cars"]` or categorized with `category: "cars" | "car"`.
* **`publishedAppliances`**: Strictly filters out drafts and future-dated posts even in local development to preserve the structural grid of the Appliances Hub.

---

## 4. Appliances Hub Specifications (`/category/appliances/`)

Articles designed to appear in the Appliances Hub must include specialized YAML frontmatter keys in `src/posts/`:

```yaml
---
title: "The Most Reliable Dishwashers of 2026"
layout: post.njk
permalink: "/most-reliable-dishwashers-2026/"
tags: ["appliances", "dishwashers"]
sub_category: "major-kitchen"       # Options: "major-kitchen", "laundry", "small-appliances"
top_pick: true                      # Optional: features article in the Top Picks hero section
cr_score: 88                        # Numerical Consumer Reports score
hook_summary: "Bosch and Miele dominate reliability testing, but one budget Whirlpool surprised testers."
brands_reviewed: "Bosch, Miele, Whirlpool, KitchenAid"
price_bracket: "$800 - $1,600"
top_winner: "Bosch 800 Series"
---
```

---

## 5. Article Publishing Workflow (`article_processing/`)

When publishing or updating articles with Amazon product cards and affiliate links, follow this automated pipeline. CRWatchdog links directly to canonical Amazon URLs using the `crwatchdog-20` Associates tag and relies on **Amazon OneLink** for international visitor geo-redirection (Geniuslink is completely retired).

### Step 1: Prepare the Draft
1. Place raw drafts in `article_processing/YOUR_DRAFT.md`.
2. Ensure standard frontmatter is declared (`title`, `description`, `date`, `layout: post.njk`, `permalink`, `tags`).
3. **Resolve Short URLs**: If the draft contains shortened URLs (`https://a.co/...`), resolve them into canonical URLs (`https://www.amazon.com/dp/ASIN`) before executing.

### Step 2: Run the Publisher Script
Run from the workspace root with environment variables loaded:
```bash
export $(cat article_processing/.env | grep -v '^#' | xargs) && python3 article_processing/publish_article_crwatchdog.py article_processing/YOUR_DRAFT.md
```

### What the Script Does Automatically:
1. Queries the Amazon Creators API / catalog for high-resolution product photography (legacy PAAPI v5 is deprecated).
2. Saves downloaded assets directly into `src/markdown_images/`.
3. Canonicalizes all Amazon product URLs into direct affiliate links (`https://www.amazon.com/dp/[ASIN]?tag=crwatchdog-20`) or targeted search queries (`https://www.amazon.com/s?k=[keywords]&tag=crwatchdog-20`). International visitors are automatically routed via Amazon OneLink.
4. Injects responsive product image blocks and branded "Check Price" CTA buttons.
5. Injects `featured_image` and `featured_image_alt` into the frontmatter.
6. Publishes the finished markdown file directly into `src/posts/` while leaving the original draft safe in `article_processing/`.

---

## 6. Editorial & Formatting Standards

### Brand Naming & Editorial Tone
* **Brand Name**: Always expand "CR" to **"Consumer Reports"** in article body copy. Do not refer to the organization as "CR" in prose.
* **Reporting Cycle / Dating**: Ensure article titles, body copy, comparisons, and dates reflect the active testing cycle (**2026**).
* **Author byline**: Default to `"CRWatchdog Team"` unless a specific author is requested.

### HTML & CSS Styling Rules
* **Images**:
  * Standard inline image styling:
    ```html
    <img src="/markdown_images/product-image.jpg" alt="Description" style="max-width: 92%; height: auto; display: block; margin: 1rem auto; border-radius: 10px;" />
    ```
  * Secondary / illustrative / side-by-side images: `max-width: 46%`.
* **Tables**:
  * **Always wrap Markdown tables in `<div class="table-wrapper">`**:
    ```html
    <div class="table-wrapper">

    | Brand & Model | CR Score | Price | Verdict |
    | :--- | :--- | :--- | :--- |
    | Bosch 800 Series | 88 | $1,299 | Top Pick |

    </div>
    ```
  * This wrapper activates horizontal scrolling on mobile and applies customized styles from `static/css/11ty-overrides.css` (zebra striping, border accents).

---

## 7. WordPress Legacy Migration Workflow

When migrating legacy WordPress static folders to clean Markdown posts:

1. **Locate Legacy Page**: Check legacy static folders under `static/page-slug/index.html`.
2. **Extract & Convert**: Create `src/posts/page-slug.md`.
3. **Preserve URL**: Set `permalink: "/page-slug/"` (with leading and trailing slashes) to maintain exact canonical URLs and search rankings.
4. **Preserve or Migrate Assets**: Reuse existing `/wp-content/uploads/` links or move new high-res assets to `src/markdown_images/`.
5. **Apply Formatting Standards**: Expand "CR" to "Consumer Reports", wrap tables in `<div class="table-wrapper">`, and add appropriate 2026 dates.
6. **Cleanup Legacy Folder**: Remove `static/page-slug/` using `rm -rf` to prevent Eleventy passthrough collision.
7. **Verify Redirects**: If the URL path changed, register a 301 redirect in root `_redirects`.

---

## 8. Development & Build Commands

```bash
# Start local development server with live reload (http://localhost:8080)
npm run dev

# Standard build into _site/
npm run build

# Production build with draft & future-dated filtering
npm run build:prod
```
