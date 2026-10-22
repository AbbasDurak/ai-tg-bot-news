"""AI Radar: Telegram bot + zamanlanmış haber taraması."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes)

import news_sources
import story_renderer
import summarizer
from config import Settings, SanitizingFilter, load_settings, sanitize
from db import Database
from models import Summary

logger = logging.getLogger("ai_radar")


def is_owner(settings: Settings, update: Update) -> bool:
    user = update.effective_user
    chat = update.effective_chat
    return (user is not None and chat is not None and settings.owner_id is not None
            and user.id == settings.owner_id and chat.id == settings.owner_id)


def format_suggestion(summary: Summary) -> str:
    parts = [f"📰 {summary.title_tr}", "", summary.summary_tr, "",
             f"💡 Neden önemli: {summary.importance_tr}", "",
             f"Kaynak: {summary.source}", summary.url]
    if summary.warning:
        parts += ["", summary.warning]
    parts += ["", "Not: İçerik otomatik üretilmiştir; yayımlamadan önce kaynağı doğrulayın."]
    return "\n".join(parts)[:4000]


def keyboard(news_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Onayla ve tasarla", callback_data=f"approve:{news_id}"),
        InlineKeyboardButton("❌ Reddet", callback_data=f"reject:{news_id}"),
    ]])


async def send_suggestion(app: Application, news_id: int) -> None:
    settings: Settings = app.bot_data["settings"]
    db: Database = app.bot_data["db"]
    summary = db.get_summary(news_id)
    if summary is None or db.get_status(news_id) != "pending":
        return
    if not db.claim_delivery(news_id, "suggestion"):
        return  # daha önce gönderilmiş
    try:
        msg = await app.bot.send_message(
            chat_id=settings.owner_id, text=format_suggestion(summary),
            reply_markup=keyboard(news_id), disable_web_page_preview=True)
        db.set_message(news_id, msg.chat_id, msg.message_id)
    except Exception as exc:
        db.release_delivery(news_id, "suggestion")  # sonraki taramada/yeniden başlatmada denenir
        logger.error("Öneri gönderilemedi: %s", sanitize(str(exc), settings))


async def scan_job(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    db: Database = app.bot_data["db"]
    logger.info("Haber taraması başladı")
    try:
        urls, hashes = db.known_urls_and_hashes()
        items = await asyncio.to_thread(
            news_sources.collect_news, urls, hashes, db.recent_titles(),
            settings.max_item_age_days)
    except Exception as exc:
        logger.error("Tarama başarısız: %s", sanitize(str(exc), settings))
        return
    for item in items[: settings.max_suggestions]:
        news_id = db.add_news(item)
        if news_id is None:
            continue
        item.id = news_id
        summary = await asyncio.to_thread(summarizer.summarize, item, settings)
        db.create_approval(news_id, summary)
        await send_suggestion(app, news_id)
    # Önceki denemede gönderilemeyen bekleyenler
    for news_id in db.pending_without_delivery():
        await send_suggestion(app, news_id)
    logger.info("Haber taraması bitti (%d aday)", len(items))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    db: Database = context.application.bot_data["db"]
    query = update.callback_query
    if query is None:
        return
    if not is_owner(settings, update):
        await query.answer("Bu işlem için yetkiniz yok.", show_alert=True)
        logger.warning("Yetkisiz callback denemesi (user_id=%s)",
                       update.effective_user.id if update.effective_user else "?")
        return
    action, _, raw_id = (query.data or "").partition(":")
    if action not in {"approve", "reject"} or not raw_id.isdigit():
        await query.answer("Geçersiz işlem.")
        return
    news_id = int(raw_id)
    news = db.get_news(news_id)
    summary = db.get_summary(news_id)
    if news is None or summary is None:
        await query.answer("Haber bulunamadı.", show_alert=True)
        return

    new_status = "approved" if action == "approve" else "rejected"
    if not db.decide(news_id, new_status):
        await query.answer("Bu haber için karar zaten verilmiş.")
        return
    await query.answer()
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    if action == "reject":
        await context.bot.send_message(settings.owner_id, "❌ Haber reddedildi; yayımlanmayacak.")
        return

    try:
        out = str(Path(settings.output_dir) / f"story_{news_id}.png")
        await asyncio.to_thread(story_renderer.render_story, summary, out, settings.template_path)
    except Exception as exc:
        db.reset_to_pending(news_id)
        logger.error("Story üretilemedi: %s", sanitize(str(exc), settings))
        await context.bot.send_message(
            settings.owner_id, "⚠️ Görsel üretilemedi. Butonlarla tekrar deneyebilirsiniz.",
            reply_markup=keyboard(news_id))
        return

    caption = story_renderer.build_caption(summary)
    if db.claim_delivery(news_id, "story_owner"):
        try:
            with open(out, "rb") as fh:
                await context.bot.send_photo(settings.owner_id, fh)
            await context.bot.send_message(
                settings.owner_id, "📋 Instagram açıklaması (kopyalayın):\n\n" + caption)
        except Exception as exc:
            db.release_delivery(news_id, "story_owner")
            db.reset_to_pending(news_id)
            logger.error("Story gönderilemedi: %s", sanitize(str(exc), settings))
            return

    if settings.auto_publish_enabled and settings.publish_chat_id \
            and db.claim_delivery(news_id, "publish"):
        try:
            with open(out, "rb") as fh:
                await context.bot.send_photo(settings.publish_chat_id, fh, caption=caption[:1024])
        except Exception as exc:
            db.release_delivery(news_id, "publish")
            logger.error("Grup/kanal yayını başarısız: %s", sanitize(str(exc), settings))
            await context.bot.send_message(settings.owner_id, "⚠️ Grup/kanal yayını başarısız oldu.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        return  # yetkisiz kullanıcılara yanıt verme
    await update.message.reply_text(
        "AI Radar çalışıyor. Haberler 08, 11, 14, 17, 20 ve 23:00'te (TSİ) taranır.\n"
        "/scan — şimdi tara")


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not is_owner(settings, update):
        return
    await update.message.reply_text("Tarama başlatıldı…")
    await scan_job(context.application)


def build_scheduler(app: Application, settings: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.timezone)
    hours = ",".join(str(h) for h in settings.scan_hours)
    scheduler.add_job(scan_job, CronTrigger(hour=hours, minute=0, timezone=settings.timezone),
                      args=[app], id="scan", max_instances=1, coalesce=True,
                      misfire_grace_time=600)
    return scheduler


async def post_init(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    scheduler = build_scheduler(app, settings)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler
    # Restart sonrası gönderilmemiş bekleyen öneriler
    for news_id in app.bot_data["db"].pending_without_delivery():
        await send_suggestion(app, news_id)


async def post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler:
        scheduler.shutdown(wait=False)


def setup_logging(settings: Settings) -> None:
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    flt = SanitizingFilter(settings)
    for handler in logging.getLogger().handlers:
        handler.addFilter(flt)
    # httpx istek URL'leri bot token'ı içerir
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main() -> int:
    settings = load_settings()
    setup_logging(settings)
    problems = settings.validate_required()
    if problems:
        for p in problems:
            logger.error(p)
        logger.error(".env dosyasını kontrol edin (bkz. .env.example).")
        return 1
    app = (Application.builder().token(settings.telegram_bot_token)
           .post_init(post_init).post_shutdown(post_shutdown).build())
    app.bot_data["settings"] = settings
    app.bot_data["db"] = Database(settings.db_path)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CallbackQueryHandler(on_callback, pattern=r"^(approve|reject):\d+$"))
    logger.info("Bot başlatılıyor")
    app.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY])
    return 0


if __name__ == "__main__":
    sys.exit(main())
