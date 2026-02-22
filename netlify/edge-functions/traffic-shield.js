export default async (request, context) => {
    const ua = request.headers.get("user-agent")?.toLowerCase() || "";

    // 1. ALLOW-LIST: These bots help your SEO and site growth
    const goodBots = [
        "googlebot", "bingbot", "perplexity", "applebot",
        "chatgpt-user", "oai-searchbot", "duckassistbot"
    ];

    if (goodBots.some(bot => ua.includes(bot))) {
        return context.next(); // Let them through!
    }

    // 2. BLOCK-LIST: Generic scrapers that eat your credits
    // These look for patterns common in automated scripts
    const badBotPatterns = ["python", "headless", "crawl", "spider", "scrap", "curl", "wget"];

    if (badBotPatterns.some(pattern => ua.includes(pattern))) {
        return new Response("Access Denied: Automated scraping is not permitted.", {
            status: 403,
            headers: { "Content-Type": "text/plain" }
        });
    }

    // 3. HUMAN USERS: Everyone else gets the site normally
    return context.next();
};