# CRWatchdog Project Handbook

Welcome! This handbook is designed to help you get up to speed with the CRWatchdog static site project.

## Project Overview
CRWatchdog is a static site built using **Eleventy (11ty)**. It aggregates reviews and information, with a particular focus on consumer electronics and cars.

## Core Technology Stack
- **Static Site Generator**: [Eleventy](https://www.11ty.dev/) (v2.0.1)
- **Templating**: Nunjucks (`.njk`) and Markdown (`.md`)
- **Logic/Processing**: Python (for article publishing and data fetching)
- **CSS**: Vanilla CSS

## Directory Structure
- [`src/`](file:///Users/timothybhattacharyya/Documents/crwatchdog-static/src): The main source directory for the Eleventy site.
  - `posts/`: Contains markdown files for articles.
  - `_includes/`: Layouts and partials.
- [`static/`](file:///Users/timothybhattacharyya/Documents/crwatchdog-static/static): Contains static assets and a large number of legacy HTML files/folders that are served directly at the root.
- [`article_processing/`](file:///Users/timothybhattacharyya/Documents/crwatchdog-static/article_processing): Python scripts for the content pipeline.
- [`_agent/`](file:///Users/timothybhattacharyya/Documents/crwatchdog-static/_agent): Audit trail of AI agent activities, plans, and walkthroughs.

## Key Workflows

### 1. Publishing Articles
Articles are processed using the scripts in `article_processing/`.
- **Script**: `publish_article_crwatchdog.py`
- **Functionality**:
    1. Fetches product images via Amazon PA-API based on ASINs found in markdown.
    2. Converts Amazon links to **GeniusLinks** for attribution/tracking.
    3. Formats the markdown with "Check Price" buttons and localized images.
    4. Places the final `.md` file in `src/posts/`.

### 2. Local Development
Run the following to preview the site:
```bash
npm run dev
```
Note: In local development, all posts (including drafts and future-dated) are visible.

## Important Configurations

### Post Visibility Logic (`.eleventy.js`)
The site has custom filtering logic for the homepage collections:
- **Production (`ELEVENTY_ENV=production`)**: Drafts (`draft: true`) and future-dated posts (where `date` > current time) are **hidden** from homepage collections.
- **Cars Collection**: Posts are categorized as "Cars" if they have the `cars` tag or a `category` value of `cars`/`car`.

## Environment Variables
Required for the article processing pipeline (stored in `.env` within `article_processing/`):
- `AMAZON_ACCESS_KEY`, `AMAZON_SECRET_KEY`, `AMAZON_TAG`
- `GENIUSLINK_API_KEY`, `GENIUSLINK_API_SECRET`, `GENIUSLINK_GROUP_ID`

---
*Last Updated: 2026-02-03 by Antigravity*
