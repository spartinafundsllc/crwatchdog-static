module.exports = function (eleventyConfig) {
  // Copy the entire Simply Static export as-is
  eleventyConfig.addPassthroughCopy({ "static": "." });

  return {
    dir: {
      input: "src",
      output: "_site"
    }
  };
};
