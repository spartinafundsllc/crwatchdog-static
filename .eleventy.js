const { DateTime } = require("luxon");

module.exports = function (eleventyConfig) {
  // ----------------------------------------
  // Pass-through static assets
  // ----------------------------------------

  // Existing: copies everything inside /static to the site root.
  // (So static/_redirects -> _site/_redirects, static/foo -> _site/foo)
  eleventyConfig.addPassthroughCopy({ static: "." });

  // Keep this (harmless even if also present under /static)
  eleventyConfig.addPassthroughCopy("_redirects");

  // NEW: copy markdown images folder to /markdown_images/*
  // src/markdown_images/my.jpg -> _site/markdown_images/my.jpg
  eleventyConfig.addPassthroughCopy({ "src/markdown_images": "markdown_images" });

  // ----------------------------------------
  // Filters
  // ----------------------------------------

  // Basic date filter (optional helper)
  eleventyConfig.addFilter("date", (value, format = "yyyy-LL-dd") => {
    if (!value) return "";
    const dt =
      value instanceof Date
        ? DateTime.fromJSDate(value, { zone: "utc" })
        : DateTime.fromJSDate(new Date(value), { zone: "utc" });

    return dt.isValid ? dt.toFormat(format) : "";
  });

  // ----------------------------------------
  // ENV: local vs production
  // ----------------------------------------
  const IS_PROD =
    process.env.ELEVENTY_ENV === "production" ||
    process.env.NODE_ENV === "production" ||
    process.env.CONTEXT === "production"; // Netlify

  const NOW = new Date();

  function normArray(v) {
    if (!v) return [];
    if (Array.isArray(v)) return v;
    if (typeof v === "string") return [v];
    return [];
  }

  function hasCarsTag(item) {
    const tags = normArray(item?.data?.tags)
      .map(String)
      .map((s) => s.toLowerCase());
    const cat = (item?.data?.category || "").toString().toLowerCase();
    return tags.includes("cars") || cat === "cars" || cat === "car";
  }

  function isDraft(item) {
    return item?.data?.draft === true;
  }

  function isFutureDated(item) {
    if (!item?.date) return false;
    const d = item.date instanceof Date ? item.date : new Date(item.date);
    return d > NOW;
  }

  function isAllowed(item) {
    if (!IS_PROD) return true; // local: show everything
    if (isDraft(item)) return false;
    if (isFutureDated(item)) return false;
    return true;
  }

  // If your homepage hero anchor is still a legacy page,
  // exclude it from Latest Articles by URL to avoid duplication.
  const HOMEPAGE_ANCHOR_URL = "/best-tvs-of-2026-according-to-consumer-reports/";
  function isAnchor(item) {
    if (!item) return false;
    if (item?.data?.homepage_anchor === true) return true;
    if (item.url === HOMEPAGE_ANCHOR_URL) return true;
    return false;
  }

  function sortNewestFirst(items) {
    return items.slice().sort((a, b) => {
      const da = a?.date ? new Date(a.date).getTime() : 0;
      const db = b?.date ? new Date(b.date).getTime() : 0;
      return db - da;
    });
  }

  // ----------------------------------------
  // Collections used by index.njk
  // ----------------------------------------

  // Latest Articles (exclude cars + exclude anchor)
  eleventyConfig.addCollection("homepageLatest", (collectionApi) => {
    const items = collectionApi.getFilteredByGlob("./src/posts/**/*.md");
    const filtered = items.filter((item) => {
      if (!isAllowed(item)) return false;
      if (isAnchor(item)) return false;
      if (hasCarsTag(item)) return false;
      return true;
    });
    return sortNewestFirst(filtered);
  });

  // Latest Cars
  eleventyConfig.addCollection("latestCars", (collectionApi) => {
    const items = collectionApi.getFilteredByGlob("./src/posts/**/*.md");
    const filtered = items.filter((item) => {
      if (!isAllowed(item)) return false;
      return hasCarsTag(item);
    });
    return sortNewestFirst(filtered);
  });

  return {
    dir: {
      input: "src",
      output: "_site",
      includes: "_includes",
      layouts: "_includes/layouts",
      data: "_data",
    },
  };
};
