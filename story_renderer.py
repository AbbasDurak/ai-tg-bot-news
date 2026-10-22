"""1080x1920 Instagram hikâye görseli ve açıklama üretimi."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from models import Summary

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1920
MARGIN = 90
ASSETS = Path(__file__).parent / "assets"

FONT_CANDIDATES = {
    False: [ASSETS / "font.ttf", "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf", "C:/Windows/Fonts/arial.ttf"],
    True: [ASSETS / "font_bold.ttf", "DejaVuSans-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "/Library/Fonts/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"],
}


def load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    logger.warning("TrueType font bulunamadı; Pillow varsayılan fontu kullanılıyor.")
    return ImageFont.load_default(size)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def fit_text(draw, text: str, max_width: int, max_height: int, start: int, minimum: int,
             bold: bool = False) -> tuple[ImageFont.ImageFont, list[str]]:
    """Metin alana sığana kadar fontu küçültür; yine sığmazsa sonuna '…' koyar."""
    for size in range(start, minimum - 1, -2):
        font = load_font(size, bold)
        lines = wrap_text(draw, text, font, max_width)
        if len(lines) * int(size * 1.3) <= max_height:
            return font, lines
    font = load_font(minimum, bold)
    lines = wrap_text(draw, text, font, max_width)
    max_lines = max(1, max_height // int(minimum * 1.3))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,;: ") + "…"
    return font, lines


def _gradient_background() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    px = ImageDraw.Draw(img)
    top, bottom = (14, 20, 48), (62, 24, 110)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        px.line([(0, y), (WIDTH, y)],
                fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img


def _cover(img: Image.Image) -> Image.Image:
    """Şablonu 1080x1920'ye oranını bozmadan doldurup ortadan kırpar."""
    img = img.convert("RGB")
    scale = max(WIDTH / img.width, HEIGHT / img.height)
    img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    left, top = (img.width - WIDTH) // 2, (img.height - HEIGHT) // 2
    return img.crop((left, top, left + WIDTH, top + HEIGHT))


def render_story(summary: Summary, output_path: str, template_path: Optional[str] = None) -> str:
    """Hikâye görselini üretip kaydeder. Şablon yoksa geçici tasarım kullanılır."""
    use_template = bool(template_path) and Path(template_path).is_file()
    if use_template:
        try:
            base = _cover(Image.open(template_path))
        except Exception as exc:
            logger.warning("Şablon açılamadı (%s); geçici tasarım kullanılıyor.",
                           exc.__class__.__name__)
            use_template = False
    if not use_template:
        base = _gradient_background()

    img = base.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # Şablondaki logo/robot görünür kalsın diye yalnızca metin bölgesine yarı saydam panel
    panel_top, panel_bottom = (620, 1700) if use_template else (300, 1620)
    od.rounded_rectangle([MARGIN - 30, panel_top, WIDTH - MARGIN + 30, panel_bottom],
                         radius=48, fill=(10, 12, 28, 190))
    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    x, max_w = MARGIN, WIDTH - 2 * MARGIN
    y = panel_top + 50
    if not use_template:
        draw.text((x, 120), "AI RADAR", font=load_font(64, True), fill=(130, 210, 255))
        draw.text((x, 200), "Yapay Zekâ Destekli Kodlama", font=load_font(34), fill=(210, 210, 230))

    total_h = panel_bottom - panel_top - 100
    title_h, summ_h, why_h = int(total_h * 0.30), int(total_h * 0.38), int(total_h * 0.22)

    font, lines = fit_text(draw, summary.title_tr, max_w, title_h, 68, 40, bold=True)
    for line in lines:
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += int(font.size * 1.3)
    y = panel_top + 50 + title_h + 20

    font, lines = fit_text(draw, summary.summary_tr, max_w, summ_h, 40, 26)
    for line in lines:
        draw.text((x, y), line, font=font, fill=(225, 228, 240))
        y += int(font.size * 1.3)
    y = panel_top + 50 + title_h + summ_h + 40

    font, lines = fit_text(draw, "Neden önemli: " + summary.importance_tr, max_w, why_h, 34, 24)
    for line in lines:
        draw.text((x, y), line, font=font, fill=(130, 210, 255))
        y += int(font.size * 1.3)

    draw.text((x, panel_bottom - 80), f"Kaynak: {summary.source}", font=load_font(34, True),
              fill=(255, 205, 110))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG")
    return output_path


def _hashtags(summary: Summary) -> list[str]:
    text = f"{summary.title_tr} {summary.summary_tr}".lower()
    tags = ["#yapayzeka"]
    if re.search(r"kod|yazılım|geliştirici|developer|agent|ajan|copilot", text):
        tags.append("#yapayzekakodlama")
    else:
        tags.append("#teknoloji")
    if re.search(r"açık kaynak|open.?source", text):
        tags.append("#acikkaynak")
    else:
        tags.append("#yapayzekahaberleri")
    return tags[:3]


def build_caption(summary: Summary) -> str:
    """Kopyalanabilir Instagram açıklaması (en fazla 3 hashtag)."""
    parts = [f"🤖 {summary.title_tr}", "", summary.summary_tr, "",
             f"💡 {summary.importance_tr}", "", f"Kaynak: {summary.source}"]
    if summary.warning:
        parts += ["", "ℹ️ Bu içerik otomatik hazırlanmıştır; yayımlamadan önce kaynağı doğrulayın."]
    parts += ["", " ".join(_hashtags(summary))]
    return "\n".join(parts)
