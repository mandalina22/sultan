# Münih Radar 📡

Münih'teki Türkleri ilgilendiren haber, etkinlik ve konserleri otomatik bulup
**Telegram'ına mesaj olarak** gönderen bot. Aylık maliyeti: **0 €**.

Sen hiçbir şey yapmazsın — bot her 30 dakikada bir kendi kendine internet
kaynaklarını tarar, ilgili bir şey bulursa telefonuna düşürür. Sen de
beğendiklerini Instagram post'una çevirirsin.

---

## KURULUM

Toplam 4 bölüm var. Sırayla git, hiçbirini atlama.
Yaklaşık 20-30 dakika sürer ve **bir kere** yapılır.

---

### BÖLÜM 1 — İki anahtar topla (10 dk)

Bir kağıda ya da telefonuna not alacağın **3 bilgi** var. Önce onları toplayalım.

#### 1a. Telegram bot token'ı

1. Telegram'ı aç, arama kutusuna **BotFather** yaz (mavi tikli olanı seç).
2. Sohbeti aç, `/newbot` yaz ve gönder.
3. Bot'una bir isim sorar → ne istersen yaz (ör. `Münih Radar`).
4. Kullanıcı adı sorar → sonu `bot` ile bitmek zorunda (ör. `munihradar_bot`).
5. BotFather sana uzun bir şifre verir, şuna benzer:
   `7123456789:AAHfk3j2...`
   👉 **Bunu not al. Bu senin TELEGRAM_BOT_TOKEN'ın.**

#### 1b. Chat ID'n (botun sana yazacağı adres)

1. Telegram aramasına **@userinfobot** yaz, sohbeti aç, **Start**'a bas.
2. Sana `Id: 123456789` gibi bir sayı döner.
   👉 **Bu sayıyı not al. Bu senin TELEGRAM_CHAT_ID'n.**
3. Son olarak kendi bot'unu bul (1a'da verdiğin kullanıcı adıyla arat),
   sohbetini aç ve **Start**'a bas. Bunu yapmazsan bot sana mesaj atamaz.

#### 1c. Gemini API anahtarı (yapay zeka için, bedava)

1. Tarayıcıda **aistudio.google.com** adresine git, Google hesabınla gir.
2. **Get API key** → **Create API key** butonlarına bas.
3. `AIza...` ile başlayan bir anahtar verir.
   👉 **Bunu not al. Bu senin GEMINI_API_KEY'in.**
   (Kart bilgisi istemez, istemeye kalkarsa yanlış yerdesin.)

✅ **Kontrol:** Elinde 3 not olmalı: bot token, chat id, gemini key.

---

### BÖLÜM 2 — Dosyaları GitHub'a yükle (5 dk)

⚠️ **En çok hata yapılan bölüm bu. Yavaş oku.**

1. Sana verilen `muenchen-radar.zip` dosyasını bilgisayarında bir klasöre
   çıkart (sağ tık → "Tümünü ayıkla" / "Extract").
2. Çıkan `muenchen-radar` klasörünü aç. İçinde şunları görmelisin:
   `config`, `src`, `.github`, `requirements.txt`, `README.md`
   (`.github` klasörünü görmüyorsan: Windows'ta "Görünüm → Gizli öğeler"i,
   Mac'te `Cmd+Shift+.` kısayolunu aç — noktayla başlayan klasörler gizlidir.)
3. GitHub'da repo'nun ana sayfasına git (**Code** sekmesi).
4. **Add file → Upload files**'a tıkla.
5. Bilgisayarındaki klasörden şunları seçip sürükle-bırak:
   **`config` klasörü, `src` klasörü, `requirements.txt`, `README.md`**
   (`.github` zaten repo'da varsa tekrar yüklemene gerek yok.)
6. Sayfanın altındaki yeşil **Commit changes** butonuna bas ve
   yükleme bitene kadar bekle.

⚠️ **Dikkat:** `muenchen-radar` klasörünün **kendisini değil, İÇİNDEKİLERİ**
yüklüyorsun. Fark şu:

```
DOĞRU ✅                 YANLIŞ ❌
repo/                    repo/
├── .github/             └── muenchen-radar/
├── config/                  ├── config/
├── src/                     ├── src/
└── requirements.txt         └── requirements.txt
```

✅ **Kontrol:** Repo ana sayfasında dosya listesinde `.github`, `config`,
`src` ve `requirements.txt` **yan yana, en dış seviyede** görünüyor olmalı.
Görünmüyorsa bot ÇALIŞMAZ — bu adımı düzeltmeden devam etme.

---

### BÖLÜM 3 — Anahtarları GitHub'a tanıt (5 dk)

Bölüm 1'de topladığın 3 notu şimdi GitHub'a gireceğiz. Bunlar "Secret"
olarak saklanır — kodda görünmez, kimse okuyamaz, repo public olsa bile.

1. Repo sayfasında üstteki **Settings** sekmesine tıkla.
2. Sol menüde **Secrets and variables → Actions**'a tıkla.
3. Yeşil **New repository secret** butonuna bas.
4. Şu üç secret'ı **tek tek** ekle. İsimleri buradan kopyala-yapıştır yap,
   elle yazma — bir harf bile farklı olursa bot çalışmaz:

   | Name (aynen böyle)   | Secret (senin notun)          |
   |----------------------|-------------------------------|
   | `TELEGRAM_BOT_TOKEN` | BotFather'ın verdiği uzun şifre |
   | `TELEGRAM_CHAT_ID`   | @userinfobot'un verdiği sayı  |
   | `GEMINI_API_KEY`     | AIza... ile başlayan anahtar  |

✅ **Kontrol:** Secrets sayfasında 3 satır listeleniyor olmalı.

---

### BÖLÜM 4 — Çalıştır (2 dk)

1. Repo sayfasında üstteki **Actions** sekmesine tıkla.
2. Sol taraftan **radar-scan**'e tıkla.
3. Sağda **Run workflow** butonuna bas → açılan kutuda yeşil
   **Run workflow**'a bir daha bas.
4. 10 saniye bekle, sayfayı yenile. Listede yeni bir çalışma belirir.
5. 2-3 dakika içinde sonuçlanır:
   - ✅ **Yeşil tik** → her şey çalıştı. Telegram'a mesaj geldiyse süper;
     gelmediyse de sorun yok, o taramada paylaşmaya değer içerik
     bulunamamıştır (aşağıdaki tabloya bak).
   - ❌ **Kırmızı çarpı** → aşağıdaki hata tablosuna bak.

Bundan sonrası otomatik: bot her 30 dakikada bir kendi kendine çalışır.
Bir daha bu sayfaya girmen gerekmez.

---

## BİR ŞEY TERS GİDERSE

Kırmızı çarpıya tıkla → **scan** yazısına tıkla → solda adımlar listelenir,
kırmızı olana tıkla → hata mesajı en alttadır. Sonra bu tabloya bak:

| Hata / Belirti | Sebep | Çözüm |
|---|---|---|
| `requirements.txt ... not found` | Dosyalar yüklenmemiş ya da klasör içinde kalmış | Bölüm 2'yi baştan yap, "DOĞRU/YANLIŞ" şemasına bak |
| `KeyError: 'GEMINI_API_KEY'` (veya başka isim) | O secret eksik ya da adı yanlış yazılmış | Bölüm 3'e dön, ismi kopyala-yapıştır ile düzelt |
| `[TELEGRAM HATA] 401` | Bot token yanlış | BotFather'daki token'ı tekrar kopyala, secret'ı güncelle |
| `[TELEGRAM HATA] 400` | Chat ID yanlış | @userinfobot'tan tekrar al; bot'una Start'a bastığından emin ol |
| Yeşil tik ama Telegram'a hiç mesaj gelmiyor | O taramalarda eşiği geçen içerik yok | Normal. Birkaç saat bekle. Hâlâ yoksa log'da `[FETCH] ... 0 item` satırı var mı bak — varsa o kaynağın adresi bozuk demektir |
| `[FETCH] KaynakAdı: 0 item` | O RSS adresi değişmiş | `config/sources.yml`'de o kaynağın adresini güncelle |

Çözemediğin bir hata olursa: kırmızı adımın log'undaki **son 5-10 satırı**
kopyalayıp yardım istediğin kişiye/AI'a yapıştır. "Hata verdi" demek yerine
o satırları göstermek çözümü 10 kat hızlandırır.

---

## GÜNLÜK KULLANIM

- Telegram'a düşen adaylardan beğendiklerini Instagram'da paylaş. Hepsi bu.
- **Çok fazla alakasız bildirim mi geliyor?** → GitHub'da `src/main.py`
  dosyasını aç (dosyaya tıkla → kalem ikonu), `MIN_SCORE = 6` satırını
  `7` yap, commit'le.
- **Yeni sanatçı/konu mu eklemek istiyorsun?** → `config/keywords.yml`
  dosyasını GitHub'da aç, listeye bir satır ekle, commit'le.
- **Yeni kaynak mı buldun?** (dernek sitesi, venue takvimi...) →
  `config/sources.yml`'e ekle. Dosyanın içindeki örneklere bak, aynı
  formatta yaz.

Bu üç dosya dışında hiçbir dosyaya dokunmana gerek yok.
