const fs = require("fs");
const path = require("path");

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

module.exports = () => {
  const staticRoot = path.join(process.cwd(), "static");

  if (!fs.existsSync(staticRoot)) return [];

  // Find all index.html files under /static, excluding wp-content and obvious non-pages
  const files = walk(staticRoot)
    .filter((p) => p.endsWith("index.html"))
    .filter((p) => !p.includes(`${path.sep}wp-content${path.sep}`))
    .filter((p) => !p.includes(`${path.sep}wp-includes${path.sep}`));

  // Convert filesystem paths to site paths
  const urls = files.map((filePath) => {
    let rel = path.relative(staticRoot, filePath); // e.g. "best-thing/index.html" or "index.html"
    rel = rel.replace(/index\.html$/, "");         // "best-thing/" or ""
    rel = rel.split(path.sep).join("/");           // normalize slashes
    return "/" + rel;                              // "/" or "/best-thing/"
  });

  // Remove duplicates + ignore root (we’ll include homepage via Eleventy or keep it from static)
  const unique = Array.from(new Set(urls));

  return unique;
};
