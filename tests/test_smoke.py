import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

import news_sources as ns
import story_renderer
import summarizer
from config import Settings, sanitize
from db import Database
from models import NewsItem, Summary


def _item(url, title, src="OpenAI"):
    return NewsItem(url=url, canonical_url=ns.canonicalize_url(url), title=title,
                    content="We are introducing a new model for developers.", source=src,
                    title_hash=ns.title_hash(title))


def test_canonicalize():
    a = ns.canonicalize_url("http://www.Example.com/post/?utm_source=x&id=1#top")
    assert a == "https://example.com/post?id=1"


def test_dedupe():
    items = [_item("https://a.com/1?utm_x=1", "OpenAI launches GPT Five model"),
             _item("https://a.com/1", "Other"),
             _item("https://b.com/2", "OpenAI launches GPT Five model!"),
             _item("https://c.com/3", "Totally different story about robots")]
    assert len(ns.dedupe(items, set(), set())) == 2


def test_db_idempotency(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    nid = db.add_news(_item("https://a.com/1", "T"))
    assert db.add_news(_item("https://a.com/1", "T")) is None
    s = Summary(title_tr="b", summary_tr="s", importance_tr="i", source="x", url="u")
    db.create_approval(nid, s)
    assert db.pending_without_delivery() == [nid]
    assert db.claim_delivery(nid, "suggestion") and not db.claim_delivery(nid, "suggestion")
    assert db.decide(nid, "approved") and not db.decide(nid, "rejected")


def test_fallback_without_key():
    s = summarizer.summarize(_item("https://a.com/1", "T"), Settings())
    assert s.used_fallback and s.warning


def test_render_and_caption(tmp_path):
    s = Summary(title_tr="Başlık " * 20, summary_tr="Özet cümlesi. " * 30,
                importance_tr="Önemli " * 20, source="OpenAI", url="u")
    out = story_renderer.render_story(s, str(tmp_path / "s.png"), "assets/yok.png")
    assert Image.open(out).size == (1080, 1920)
    tpl = tmp_path / "tpl.png"
    Image.new("RGB", (500, 900), "red").save(tpl)
    out = story_renderer.render_story(s, str(tmp_path / "s2.png"), str(tpl))
    assert Image.open(out).size == (1080, 1920)
    assert story_renderer.build_caption(s).count("#") <= 3


def test_sanitize():
    assert "123456:" not in sanitize("https://api.telegram.org/bot123456:" + "A" * 30 + "/x")
