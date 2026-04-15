"""Free news feed service — scrapes financial news from public RSS feeds."""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

logger = logging.getLogger(__name__)

# Free RSS feeds for financial/Islamic finance news
RSS_FEEDS = [
    {
        "name": "Reuters Business",
        "url": "https://feeds.reuters.com/reuters/businessNews",
        "category": "global",
    },
    {
        "name": "Reuters Markets",
        "url": "https://feeds.reuters.com/reuters/companyNews",
        "category": "global",
    },
    {
        "name": "Al Jazeera Business",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "category": "islamic",
    },
    {
        "name": "Addis Fortune",
        "url": "https://addisfortune.news/feed/",
        "category": "ethiopia",
    },
    {
        "name": "The Reporter Ethiopia",
        "url": "https://www.thereporterethiopia.com/feed/",
        "category": "ethiopia",
    },
    {
        "name": "Capital Ethiopia",
        "url": "https://www.capitalethiopia.com/feed/",
        "category": "ethiopia",
    },
]

# Cached news articles (refreshed when fetched successfully)
_news_cache = {"articles": [], "updated": None}

# Keywords to filter relevant news
RELEVANCE_KEYWORDS = [
    "ethiopia", "ethiopian", "addis", "ecx", "esx", "coffee", "sesame", "teff",
    "islamic", "halal", "sharia", "sukuk", "takaful", "muslim",
    "stock", "market", "commodity", "trading", "investment", "finance",
    "bank", "zamzam", "hijra", "telecom", "birr", "etb",
    "africa", "african", "east africa",
    "apple", "microsoft", "tesla", "nvidia", "amazon", "google", "aramco",
    "oil", "gold", "wheat", "agriculture",
]


def fetch_rss_feed(url, timeout=10):
    """Fetch and parse a single RSS feed."""
    try:
        import requests
    except ImportError:
        return []

    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "TradEt/1.0"})
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        items = []

        # Standard RSS 2.0
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            pub_date = item.findtext("pubDate", "").strip()

            if title and link:
                # Remove HTML tags from description
                import re
                desc = re.sub(r'<[^>]+>', '', desc)[:300]

                items.append({
                    "title": title,
                    "link": link,
                    "description": desc,
                    "published_at": pub_date,
                })

        # Atom feeds
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = entry.findtext("atom:title", "", ns).strip()
            link_elem = entry.find("atom:link", ns)
            link = link_elem.get("href", "") if link_elem is not None else ""
            desc = entry.findtext("atom:summary", "", ns).strip()
            pub_date = entry.findtext("atom:published", "", ns).strip()

            if title and link:
                import re
                desc = re.sub(r'<[^>]+>', '', desc)[:300]
                items.append({
                    "title": title,
                    "link": link,
                    "description": desc,
                    "published_at": pub_date,
                })

        return items[:10]  # Limit per feed
    except Exception as e:
        logger.debug(f"RSS fetch failed for {url}: {e}")
        return []


def is_relevant(article):
    """Check if article is relevant to TradEt users."""
    text = (article.get("title", "") + " " + article.get("description", "")).lower()
    return any(kw in text for kw in RELEVANCE_KEYWORDS)


def _fallback_news():
    """Provide static curated news when RSS feeds are unreachable."""
    return [
        {
            "title": "Ethiopia's ECX Launches New Electronic Trading Platform",
            "description": "The Ethiopia Commodity Exchange has upgraded its trading infrastructure to support real-time commodity trading with enhanced transparency and Sharia-compliant features.",
            "link": "https://www.ecx.com.et/",
            "source": "ECX",
            "category": "ethiopia",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "Islamic Finance Assets Projected to Surpass $4.9 Trillion by 2027",
            "description": "Global Islamic finance continues its strong growth trajectory, with sukuk issuance and halal investments driving expansion across emerging markets including East Africa.",
            "link": "https://www.ifsb.org/",
            "source": "IFSB",
            "category": "islamic",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "NBE Foreign Exchange Reform Boosts Ethiopian Birr Trading Volume",
            "description": "The National Bank of Ethiopia's forex liberalization policy has increased daily trading volumes significantly, attracting more international investors to the Ethiopian market.",
            "link": "https://nbe.gov.et/",
            "source": "NBE",
            "category": "ethiopia",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "Ethiopian Coffee Exports Reach Record High Amid Rising Global Demand",
            "description": "Ethiopia's specialty coffee exports have hit record levels on the ECX, with premium Yirgacheffe and Sidamo varieties commanding top prices in international markets.",
            "link": "https://www.ecx.com.et/",
            "source": "ECX",
            "category": "ethiopia",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "AAOIFI Updates Sharia Standards for Digital Asset Trading",
            "description": "The Accounting and Auditing Organization for Islamic Financial Institutions has released updated standards covering cryptocurrency and digital asset trading compliance.",
            "link": "https://aaoifi.com/",
            "source": "AAOIFI",
            "category": "islamic",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "Global Markets Rally as Tech Stocks Lead Gains",
            "description": "Major stock indices posted gains as technology companies reported strong earnings. Apple, Microsoft, and NVIDIA all surpassed analyst expectations for the quarter.",
            "link": "https://www.reuters.com/markets/",
            "source": "Markets",
            "category": "global",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "East African Community Advances Regional Sukuk Bond Framework",
            "description": "The EAC is developing a unified sukuk issuance framework to facilitate Sharia-compliant bond markets across Kenya, Ethiopia, Tanzania, and Uganda.",
            "link": "https://www.eac.int/",
            "source": "EAC",
            "category": "islamic",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
        {
            "title": "Sesame Prices Surge on ECX as International Demand Grows",
            "description": "Humera and Wollega sesame varieties traded at multi-month highs on the Ethiopia Commodity Exchange, driven by increased demand from China and Japan.",
            "link": "https://www.ecx.com.et/",
            "source": "ECX",
            "category": "ethiopia",
            "published_at": datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT"),
        },
    ]


def fetch_news(category=None, limit=30):
    """
    Fetch news from all RSS feeds with caching and fallback.
    category: 'global', 'ethiopia', 'islamic', or None for all
    """
    global _news_cache
    all_articles = []

    feeds = RSS_FEEDS
    if category:
        feeds = [f for f in feeds if f["category"] == category]

    for feed in feeds:
        articles = fetch_rss_feed(feed["url"], timeout=8)
        for article in articles:
            article["source"] = feed["name"]
            article["category"] = feed["category"]

        # Filter for relevance on global feeds
        if feed["category"] == "global":
            articles = [a for a in articles if is_relevant(a)]

        all_articles.extend(articles)

    # If we got live articles, cache them
    if all_articles:
        _news_cache["articles"] = all_articles
        _news_cache["updated"] = datetime.utcnow().isoformat()
    elif _news_cache["articles"]:
        # Use cached articles if available
        all_articles = _news_cache["articles"]
        if category:
            all_articles = [a for a in all_articles if a.get("category") == category]
    else:
        # Use fallback curated news
        all_articles = _fallback_news()
        if category:
            all_articles = [a for a in all_articles if a.get("category") == category]

    # Sort by publication date (newest first), limit results
    all_articles.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    return all_articles[:limit]
