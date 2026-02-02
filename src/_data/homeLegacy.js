// src/_data/homeLegacy.js
const fs = require("fs");
const path = require("path");

function pick(re, s) {
  if (!s) return "";
  const m = s.match(re);
  if (!m || m.length < 2 || m[1] == null) return "";
  return String(m[1]).trim();
}

function unescapeBasic(s) {
  return (s || "")
    .replace(/&amp;/g, "&")
    .replace(/&#8217;/g, "’")
    .replace(/&#8220;/g, "“")
    .replace(/&#8221;/g, "”");
}

function stripTags(s) {
  return (s || "").replace(/<[^>]+>/g, "").trim();
}

function parseArticleBlock(html) {
  if (!html) return null;

  const articleHtml = html.toLowerCase().includes("<article")
    ? html
    : pick(/(<article[\s\S]*?<\/article>)/i, html);

  if (!articleHtml) return null;

  const url = pick(/<a[^>]+href="([^"]+)"/i, articleHtml);
  const img = pick(/<img[^>]+src="([^"]+)"/i, articleHtml);

  const rawTitle = pick(
    /<h2[^>]*class="entry-title"[^>]*>[\s\S]*?<a[^>]*>([\s\S]*?)<\/a>/i,
    articleHtml
  );

  const title = unescapeBasic(stripTags(rawTitle));

  const rawExcerpt = pick(
    /<div class="entry-content">[\s\S]*?<p>([\s\S]*?)<\/p>/i,
    articleHtml
  );

  const excerpt = stripTags(unescapeBasic(rawExcerpt))
    .replace(/\s*Read More\s*$/i, "")
    .trim();

  if (!url && !title) return null;

  return { url, img, title, excerpt };
}

function parseMultiArticles(sectionHtml) {
  if (!sectionHtml) return [];
  const matches = sectionHtml.match(/<article[\s\S]*?<\/article>/gi) || [];
  return matches.map((a) => parseArticleBlock(a)).filter(Boolean);
}

module.exports = function () {
  // CHANGED: point to your moved legacy homepage file
  const legacyPath = path.join(process.cwd(), "static", "_legacy", "index_old.html");

  if (!fs.existsSync(legacyPath)) {
    return {
      found: false,
      mainFeatured: null,
      latest: { main: null, twoCol: [], threeCol: [] },
      cars: [],
    };
  }

  const html = fs.readFileSync(legacyPath, "utf8");

  const featured2 = pick(/(<section[^>]+id="featured-post-2"[\s\S]*?<\/section>)/i, html);
  const mainFeatured = featured2 ? parseArticleBlock(featured2) : null;

  const featured3 = pick(/(<section[^>]+id="featured-post-3"[\s\S]*?<\/section>)/i, html);
  const latestMain = featured3 ? parseArticleBlock(featured3) : null;

  const featured4 = pick(/(<section[^>]+id="featured-post-4"[\s\S]*?<\/section>)/i, html);
  const latestTwoCol = featured4 ? parseMultiArticles(featured4) : [];

  const featured5 = pick(/(<section[^>]+id="featured-post-5"[\s\S]*?<\/section>)/i, html);
  const latestThreeCol = featured5 ? parseMultiArticles(featured5) : [];

  const featured7 = pick(/(<section[^>]+id="featured-post-7"[\s\S]*?<\/section>)/i, html);
  const cars = featured7 ? parseMultiArticles(featured7) : [];

  return {
    found: true,
    mainFeatured,
    latest: { main: latestMain, twoCol: latestTwoCol, threeCol: latestThreeCol },
    cars,
  };
};
