"""Ortak veri modelleri."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class NewsItem(BaseModel):
    id: Optional[int] = None
    url: str
    canonical_url: str
    title: str
    content: str = ""
    source: str
    published_at: Optional[str] = None
    title_hash: str = ""
    score: float = 0.0


class Summary(BaseModel):
    title_tr: str
    summary_tr: str
    importance_tr: str
    source: str
    url: str
    warning: str = ""
    used_fallback: bool = False
