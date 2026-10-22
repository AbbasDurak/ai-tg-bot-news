# AI Radar — Telegram Yapay Zekâ Haber Botu

Güvenilir yapay zekâ kaynaklarını (OpenAI, Anthropic, Google DeepMind, Microsoft, Meta AI,
Hugging Face, NVIDIA, GitHub vb.) RSS/Atom ile tarar, haberleri Türkçe özetler ve **sahibinin
Telegram onayıyla** Instagram hikâyesi görseli (1080×1920) ve açıklaması üretir.

## Nasıl çalışır?
1. Türkiye saatiyle 08:00, 11:00, 14:00, 17:00, 20:00, 23:00'te kaynaklar taranır
   (`/scan` komutuyla elle de tetiklenebilir).
2. Tekrarlar URL kanonikleştirme, başlık hash'i ve başlık benzerliği ile elenir; haberler
   basit bir skorla (ürün duyurusu, model, araştırma, açık kaynak, geliştirici araçları,
   ajanlar, güvenlik) sıralanır.
3. Groq ile Türkçe başlık, 2–3 cümlelik özet ve önem nedeni üretilir. Groq başarısız olursa
   yerel yedek özet + uyarı gösterilir.
4. Öneri yalnızca sahibin özel sohbetine **✅ Onayla ve tasarla / ❌ Reddet** butonlarıyla gelir.
5. Onayda görsel + kopyalanabilir açıklama (en fazla 3 hashtag) sahibine gönderilir.
   Reddedilen haber hiçbir yere yayımlanmaz.

## Kurulum
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # ardından .env dosyasını düzenleyin
python main.py
```

### Telegram bot token'ı
Telegram'da **@BotFather** ile `/newbot` komutunu kullanın; verdiği token'ı yalnızca `.env`
dosyasındaki `TELEGRAM_BOT_TOKEN` alanına yazın. Token'ı kimseyle paylaşmayın/commit'lemeyin.
Ardından botunuza özel sohbette bir kez `/start` yazın (bot, kullanıcıya ilk mesajı
ancak siz başlattıktan sonra gönderebilir).

### Owner (sahip) kullanıcı kimliği
Telegram'da **@userinfobot** gibi bir bota yazarak sayısal kullanıcı kimliğinizi öğrenin ve
`TELEGRAM_OWNER_ID` olarak girin. Yalnızca bu kullanıcı, kendi özel sohbetinde botu
kullanabilir; diğer kullanıcıların butonları ve komutları reddedilir.

## Ortam değişkenleri
| Değişken | Zorunlu | Varsayılan | Açıklama |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Evet | — | BotFather token'ı |
| `TELEGRAM_OWNER_ID` | Evet | — | Yetkili Telegram kullanıcı kimliği (sayı) |
| `GROQ_API_KEY` | Hayır | boş | Boşsa yerel yedek özet kullanılır |
| `GROQ_MODEL` | Hayır | `llama-3.3-70b-versatile` | Groq model adı |
| `DB_PATH` | Hayır | `ai_radar.db` | SQLite dosyası (bekleyen onaylar burada saklanır) |
| `OUTPUT_DIR` | Hayır | `output` | Üretilen görsellerin klasörü |
| `STORY_TEMPLATE_PATH` | Hayır | `assets/story_template.png` | Hikâye şablonu yolu |
| `MAX_SUGGESTIONS_PER_SCAN` | Hayır | `3` | Tarama başına en fazla öneri |
| `MAX_ITEM_AGE_DAYS` | Hayır | `3` | Bundan eski haberler yok sayılır |
| `TIMEZONE` | Hayır | `Europe/Istanbul` | Zamanlayıcı saat dilimi |
| `SCAN_HOURS` | Hayır | `8,11,14,17,20,23` | Tarama saatleri (virgülle) |
| `LOG_LEVEL` | Hayır | `INFO` | Log seviyesi |
| `AUTO_PUBLISH_ENABLED` | Hayır | `false` | `true` ise onaylanan içerik grup/kanala da gönderilir |
| `PUBLISH_CHAT_ID` | Hayır | boş | Yayın yapılacak grup/kanal kimliği (botun yazma yetkisi olmalı) |

Grup/kanal yayını varsayılan olarak **kapalıdır** ve açık olsa bile yalnızca sahibin onayından
sonra çalışır.

## Hikâye şablonu
Şablonunuzu `assets/story_template.png` olarak ekleyin (önerilen 1080×1920; farklı oranlar
ortadan kırpılarak doldurulur). Şablon varsa logo/robot çizimi korunur; metin, alt kısımdaki
yarı saydam panele (yaklaşık y=620–1700) yazılır, bu yüzden önemli çizimleri üst bölgede
tutun. Şablon **yoksa** bot geçici bir gradyan tasarım üretir; var olmayan bir şablon
kullanıyormuş gibi davranmaz. İsteğe bağlı olarak `assets/font.ttf` ve `assets/font_bold.ttf`
ile yazı tipi değiştirilebilir.

## Veritabanı
SQLite tabloları: `news_items`, `approvals` (pending/approved/rejected), `deliveries`
(tekrar gönderimi önleyen idempotency kayıtları). Yeniden başlatmada bekleyen onaylar
korunur; Telegram'a gönderilememiş öneriler yeniden denenir.

## Test
```bash
python -m py_compile *.py
pip install pytest && python -m pytest -q tests
```

## Replit'e taşıma
1. Depoyu Replit'e içe aktarın; Run komutunu `python main.py` yapın.
2. Değişkenleri `.env` yerine **Secrets** bölümüne girin.
3. Botun 7/24 çalışması için Always On / Reserved VM gerekir; SQLite dosyasının
   kalıcı depolamada olduğundan emin olun.

## Bilinen sınırlamalar
- Instagram'a otomatik paylaşım yoktur; görsel ve açıklama elle yüklenir.
- Bazı kaynakların RSS adresleri değişebilir veya hiç sunulmayabilir; erişilemeyen akışlar
  loglanıp atlanır. Listeyi `news_sources.py` içindeki `SOURCES` ile güncelleyin.
- Özetler yapay zekâ ürünüdür ve hata içerebilir; yayımlamadan önce kaynağı doğrulayın.
- Groq yedek özeti çeviri yapmaz, kaynağın özgün dilini kullanır.
- Tek sahip/tek süreç için tasarlanmıştır; haber skoru basit anahtar kelime tabanlıdır.
- Bu ortamda gerçek Telegram/Groq/RSS ağ çağrıları test edilmemiştir; yalnızca birim/smoke testleri çalıştırılmıştır.
