"""Haber kaynakları, toplama, kanonikleştirme ve deduplikasyon."""
from __future__ import annotations

import calendar
import hashlib
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests

from models import NewsItem

logger = logging.getLogger(__name__)

# (kaynak adı, RSS/Atom adresi). Erişilemeyen akışlar atlanır; adresler .env ile değil
# burada güncellenir. Bazı akışlar zamanla değişebilir (bkz. README "Bilinen sınırlamalar").
SOURCES: list[tuple[str, str]] = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Anthropic", "https://www.anthropic.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Google AI", "https://blog.google/technology/ai/rss/"),
    ("Microsoft AI", "https://blogs.microsoft.com/ai/feed/"),
    ("Meta AI", "https://ai.meta.com/blog/rss/"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("NVIDIA", "https://blogs.nvidia.com/feed/"),
    ("GitHub Blog", "https://github.blog/ai-and-ml/feed/"),
    ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
]

REQUEST_TIMEOUT = 15
MAX_FEED_BYTES = 5 * 1024 * 1024
USER_AGENT = "ai-radar-bot/1.0 (+https://github.com/AbbasDurak/ai-tg-bot-news)"

TRACKING_PARAMS = {"fbclid", "gclid", "ref", "ref_src", "mc_cid", "mc_eid", "igshid", "source"}

# Önceliklendirme: (anahtar kelimeler, ağırlık)
PRIORITY_KEYWORDS: list[tuple[tuple[str, ...], float]] = [
    (("introducing", "launch", "announc", "release", "available", "unveil"), 3.0),  # ürün duyurusu
    (("model", "gpt", "claude", "gemini", "llama", "mistral", "qwen", "deepseek"), 3.0),  # model
    (("agent", "agentic", "copilot", "codex", "coding", "developer", "sdk", "api", "cursor"), 2.5),
    (("open source", "open-source", "open weights", "open-weight"), 2.5),  # açık kaynak
    (("research", "paper", "benchmark", "breakthrough"), 2.0),  # araştırma
    (("security", "safety", "vulnerab", "jailbreak", "privacy", "alignment"), 2.0),  # güvenlik
]
SOURCE_WEIGHT = {
    "OpenAI": 2.0, "Anthropic": 2.0, "Google DeepMind": 2.0, "Google AI": 1.5,
    "Microsoft AI": 1.5, "Meta AI": 1.5, "Hugging Face": 1.5, "NVIDIA": 1.0, "GitHub Blog": 1.5,
}


def canonicalize_url(url: str) -> str:
    """Takip parametrelerini, fragment'ı ve sondaki '/' işaretini temizler."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title.lower())).strip()


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:32]


def _tokens(text: str) -> set[str]:
    return {t for t in normalize_title(text).split() if len(t) > 2}


def similarity(a: str, b: str) -> float:
    """Jaccard benzerliği (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def score_item(item: NewsItem, now: Optional[datetime] = None) -> float:
    now = now or datetime.now(timezone.utc)
    haystack = f"{item.title} {item.content[:600]}".lower()
    score = SOURCE_WEIGHT.get(item.source, 0.5)
    for words, weight in PRIORITY_KEYWORDS:
        if any(w in haystack for w in words):
            score += weight
    if item.published_at:
        try:
            age_h = (now - datetime.fromisoformat(item.published_at)).total_seconds() / 3600
            score += max(0.0, 3.0 - age_h / 24)  # yeni haber bonusu
        except ValueError:
            pass
    return round(score, 2)


def _entry_datetime(entry) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc)
    return None


def fetch_feed(name: str, url: str) -> list[NewsItem]:
    """Tek bir akışı çeker; hata durumunda boş liste döner (asla exception fırlatmaz)."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        if len(resp.content) > MAX_FEED_BYTES:
            logger.warning("Akış çok büyük, atlandı: %s", name)
            return []
        parsed = feedparser.parse(resp.content)
    except requests.RequestException as exc:
        logger.warning("Akış alınamadı (%s): %s", name, exc.__class__.__name__)
        return []
    except Exception as exc:  # bozuk akışlar tüm taramayı durdurmamalı
        logger.warning("Akış çözümlenemedi (%s): %s", name, exc.__class__.__name__)
        return []
    if parsed.bozo and not parsed.entries:
        logger.warning("Bozuk akış (%s)", name)
        return []

    items: list[NewsItem] = []
    for entry in parsed.entries:
        link = (entry.get("link") or "").strip()
        title = strip_html(entry.get("title") or "")
        if not link.startswith(("http://", "https://")) or not title:
            continue
        summary = entry.get("summary") or ""
        if not summary and entry.get("content"):
            summary = entry["content"][0].get("value", "")
        published = _entry_datetime(entry)
        items.append(NewsItem(
            url=link,
            canonical_url=canonicalize_url(link),
            title=title,
            content=strip_html(summary)[:2000],
            source=name,
            published_at=published.isoformat() if published else None,
            title_hash=title_hash(title),
        ))
    return items


def dedupe(
    items: list[NewsItem],
    known_urls: set[str],
    known_hashes: set[str],
    known_titles: Optional[list[str]] = None,
    threshold: float = 0.8,
) -> list[NewsItem]:
    """URL, başlık hash'i ve başlık benzerliğine göre kopyaları ayıklar."""
    seen_urls, seen_hashes = set(known_urls), set(known_hashes)
    titles = list(known_titles or [])
    unique: list[NewsItem] = []
    for item in items:
        if item.canonical_url in seen_urls or item.title_hash in seen_hashes:
            continue
        if any(similarity(item.title, t) >= threshold for t in titles):
            continue
        seen_urls.add(item.canonical_url)
        seen_hashes.add(item.title_hash)
        titles.append(item.title)
        unique.append(item)
    return unique


def collect_news(
    known_urls: set[str],
    known_hashes: set[str],
    known_titles: Optional[list[str]] = None,
    max_age_days: int = 3,
    sources: Optional[list[tuple[str, str]]] = None,
) -> list[NewsItem]:
    """Tüm kaynakları tarar; skorlanmış, tekrarsız haberleri yüksek skordan düşüğe döndürür."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    collected: list[NewsItem] = []
    for name, url in (sources or SOURCES):
        for item in fetch_feed(name, url):
            if item.published_at and datetime.fromisoformat(item.published_at) < cutoff:
                continue
            item.score = score_item(item)
            collected.append(item)
    collected.sort(key=lambda i: i.score, reverse=True)
    return dedupe(collected, known_urls, known_hashes, known_titles)
