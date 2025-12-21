const { DateTime } = require("luxon");

module.exports = function (eleventyConfig) {
  // Copy the entire Simply Static export as-is
  eleventyConfig.addPassthroughCopy({ static: "." });
  eleventyConfig.addPassthroughCopy("_redirects");

  // Nunjucks date filter: {{ date | date("yyyy-MM-dd") }}
  eleventyConfig.addFilter("date", (value, format = "yyyy-LL-dd") => {
    if (!value) return "";

    // Accept Date objects, ISO strings, etc.
    let dt;
    if (value instanceof Date) {
      dt = DateTime.fromJSDate(value, { zone: "utc" });
    } else if (typeof value === "string") {
      // Try ISO first
      dt = DateTime.fromISO(value, { zone: "utc" });
      if (!dt.isValid) {
        // Fall back to JS Date parsing
        dt = DateTime.fromJSDate(new Date(value), { zone: "utc" });
      }
    } else {
      dt = DateTime.fromJSDate(new Date(value), { zone: "utc" });
    }

    return dt.isValid ? dt.toFormat(format) : "";
  });

  return {
    dir: {
      input: "src",
      output: "_site",
    },
  };
};
