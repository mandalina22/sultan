# Münih Radar 📡

Münih'te yaşayan Türkçe konuşan toplum için haber ve etkinlik radarı.
Kaynakları tarar, kelime filtresinden geçirir, Gemini ile "gerçekten önemli
mi?" diye doğrular ve sadece geçenleri Telegram'a düşürür. Tamamen ücretsiz
katmanlarda çalışır (GitHub Actions + Gemini free tier + Telegram).

```
kaynaklar ──► tarama içi dedup ──► daha önce görülenleri ele
          ──► kelime ön filtresi (GENİŞ) ──► LLM hakemliği (DAR)
          ──► Telegram (puana göre sıralı)
```

## Kurulum (5 dakika)

1. **Telegram**: @BotFather'dan bot oluştur → token. Botla bir kere konuş,
   sonra `https://api.telegram.org/bot<TOKEN>/getUpdates` adresinden
   `chat.id` değerini al.
2. **Gemini**: https://aistudio.google.com → "Get API key" → ücretsiz anahtar.
   Kredi kartı gerekmez.
3. **Ticketmaster** (etkinlik taraması için, isteğe bağlı ama önerilir):
   https://developer.ticketmaster.com → ücretsiz hesap → Consumer Key.
4. **Bandsintown** (isteğe bağlı): https://artists.bandsintown.com/support
   üzerinden ücretsiz `app_id` iste. Anahtar yoksa bu hat kapalı kalır —
   API şartları kayıtlı anahtar istiyor, kaçak kullanmıyoruz.
5. GitHub repo → **Settings → Secrets and variables → Actions** → şunları ekle:

   | Secret                 | Zorunlu | Ne için                    |
   |------------------------|---------|----------------------------|
   | `TELEGRAM_BOT_TOKEN`   | evet    | bildirim                   |
   | `TELEGRAM_CHAT_ID`     | evet    | bildirim                   |
   | `GEMINI_API_KEY`       | önerilir| LLM doğrulaması            |
   | `TICKETMASTER_API_KEY` | önerilir| konser/etkinlik taraması   |
   | `BANDSINTOWN_APP_ID`   | hayır   | ek konser kaynağı          |

   İsteğe bağlı **Variable**: `GEMINI_MODEL` (boş bırakılırsa bot kendisi
   çalışan modeli bulur: 2.5-flash-lite → 2.5-flash → 2.0-flash).

6. **Actions** sekmesinde üç iş görünür. İlk çalıştırmayı elle yap:
   *Kaynak sağlık testi* → "Run workflow". Ölü kaynak varsa Telegram'a düşer.

> Repo **public** kalmalı: public repolarda Actions dakikası sınırsız.
> Private yaparsan aylık 2.000 dakika kotası var; saatlik tarama ~1.100
> dakika/ay eder, sığar ama pay dar kalır.

## Günlük çalışma

| İş                    | Ne zaman            | Ne yapar                                  |
|-----------------------|---------------------|-------------------------------------------|
| Haber ve gündem       | saatte bir          | haber, resmi duyuru, MVG/NINA uyarıları   |
| Etkinlik ve konser    | günde 2 (10:00, 18:00 yaz saati) | mekanlar + Ticketmaster + Bandsintown; sabah turu günlük rapor atar |
| Kaynak sağlık testi   | pazartesi           | ölü feed'leri bildirir                    |

Bot her turun sonunda `data/seen.json`'ı repoya geri commit'ler; böylece
aynı haber iki kez gitmez.

## Yerelde deneme

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... GEMINI_API_KEY=...
python -m src.main --ping             # Telegram bağlantısı
python -m src.main --test-sources     # hangi kaynak kaç item veriyor
python -m src.main --dry-run          # her şeyi yap, gönderme, state'e yazma
python -m src.main --events --dry-run # etkinlik taraması
```

## Ayar dosyaları — dokunman gereken sadece bunlar

| Dosya                 | İçerik                                                  |
|-----------------------|---------------------------------------------------------|
| `config/sources.yml`  | kaynaklar (RSS / HTML / JSON API), grup, yedek URL      |
| `config/keywords.yml` | kelime grupları — kural açıklamaları dosyanın başında   |
| `config/artists.yml`  | sanatçı, grup, komedi, talkshow, tiyatro isimleri       |

**Kelime işaretleri** (`keywords.yml` ve `artists.yml`):

- `"=kelime"` → tam kelime eşleşmesi
- `"~kelime"` → tam kelime **ve** metinde sahne bağlamı (konzert, tour,
  bilet, show…) olmalı. Türkçede günlük anlamı olan sanatçı adları için
  şart: `~Duman`, `~Ceza`, `~Elif`, `~Sıla`.
- işaretsiz, 6+ harf → alt-dize (`einbürgerung` → `Einbürgerungstest`)
- işaretsiz, kısa → kelime başı + ek (`türk` → `Türken`, `türkisch`)

## Neden güvenilir

- **Bir şey kaçmasın**: ön filtre geniş; Almanca çekim/birleşik kelimeler
  yakalanır; her kaynak için yedek URL; ölen kaynak 6 turdan sonra
  Telegram'a bildirilir.
- **Gürültü geçmesin**: her aday LLM'den 1–10 puan alır; 6 altı gitmez.
- **Veri kaybolmasın**: gönderilemeyen mesaj ve kota yüzünden puanlanamayan
  aday `seen`'e yazılmaz, sonraki turda tekrar denenir. `--dry-run` state'e
  dokunmaz.
- **Çökmesin**: Gemini anahtarı yoksa/kota bittiyse kelime moduna düşer ve
  mesajlara ⚠️ koyar; Telegram 429'da bekler; feed indirme timeout'lu;
  eş zamanlı iki run `seen.json`'ı ezmesin diye sıraya girer.

## API'yi neden az kullanıyor (0 € hedefi)

- Konsolosluk ve NINA uyarıları zaten Türkçe ve resmi → LLM'e hiç gitmez.
- Aynı haberin gazete kopyaları LLM'e bir kez gider.
- `~` işaretli belirsiz isimler bağlam olmadan aday olmaz.
- 25'lik batch'ler, kısaltılmış özetler, kısa JSON çıktısı
  (`[nr,puan,kat,özet]`), 6 altı puana özet istenmez.
- Tipik gün: 24 haber turu + 2 etkinlik turu ≈ 30–60 Gemini isteği,
  ~80k token. Ücretsiz kota model başına ~1.000–1.500 istek/gün ve
  ~250k token/dakika; %5'ini bile kullanmıyoruz. Kredi kartı bağlı
  değilse Google zaten ücret kesemez — kota dolarsa 429 döner, bot
  bekler/erteler, fatura çıkmaz.

## Yasal çerçeve

Bot yalnızca sitelerin kendi yayınladığı RSS beslemelerini, herkese açık
resmi API'leri (MVG, NINA, Ticketmaster, Bandsintown — anahtarlı) ve
robots.txt'e uyarak herkese açık HTML sayfalarını okur. Kendini dürüst bir
User-Agent ile tanıtır, giriş gerektiren hiçbir yere girmez, içerik
kopyalamaz; sadece başlık + link toplar ve kaynağa yönlendirir.
