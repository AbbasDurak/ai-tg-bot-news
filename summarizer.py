"""Groq ile Türkçe özetleme ve yerel yedek."""
from __future__ import annotations

import json
import logging
import re

import requests

from config import Settings, sanitize
from models import NewsItem, Summary

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = (
    "Sen yapay zekâ haberlerini Türkçe özetleyen titiz bir editörsün. Yalnızca verilen "
    "metne dayan; metinde olmayan bilgi, rakam veya iddia ekleme. Doğrulanmamış veya "
    "kaynağın 'iddia ettiği' bilgileri kesin ifade etme ('...iddia ediliyor', '...duyurdu' "
    "gibi atıf kullan). Yalnızca geçerli JSON döndür: "
    '{"title_tr": "kısa Türkçe başlık", "summary_tr": "2-3 cümlelik özet", '
    '"importance_tr": "bu haberin neden önemli olduğu, 1 cümle"}'
)


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("JSON bulunamadı")
    return json.loads(match.group(0))


def fallback_summary(item: NewsItem, reason: str) -> Summary:
    """Groq kullanılamadığında basit yerel özet (çeviri yapılmaz)."""
    sentences = re.split(r"(?<=[.!?])\s+", item.content.strip()) if item.content else []
    body = " ".join(sentences[:2])[:400] or "Özet alınamadı; ayrıntılar için kaynağa bakın."
    return Summary(
        title_tr=item.title,
        summary_tr=body,
        importance_tr="Önem değerlendirmesi otomatik üretilemedi; kaynağı kontrol edin.",
        source=item.source,
        url=item.url,
        warning=f"⚠️ Otomatik Türkçe özet kullanılamadı ({reason}); metin kaynağın özgün "
                "dilindedir ve doğrulanmamıştır.",
        used_fallback=True,
    )


def summarize(item: NewsItem, settings: Settings) -> Summary:
    if not settings.groq_api_key:
        return fallback_summary(item, "GROQ_API_KEY tanımlı değil")
    user_msg = (
        f"Kaynak: {item.source}\nBaşlık: {item.title}\nİçerik: {item.content[:1800]}"
    )
    try:
        resp = requests.post(
            GROQ_URL,
            headers={"Authorization": "Bearer " + settings.groq_api_key},
            json={
                "model": settings.groq_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.2,
                "max_tokens": 500,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = _extract_json(resp.json()["choices"][0]["message"]["content"])
        title = str(data["title_tr"]).strip()
        summary = str(data["summary_tr"]).strip()
        importance = str(data["importance_tr"]).strip()
        if not (title and summary and importance):
            raise ValueError("Eksik alan")
        return Summary(title_tr=title, summary_tr=summary, importance_tr=importance,
                       source=item.source, url=item.url)
    except Exception as exc:
        logger.warning("Groq özeti başarısız: %s", sanitize(f"{exc.__class__.__name__}", settings))
        return fallback_summary(item, "Groq hatası")
