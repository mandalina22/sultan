"""LLM doğrulaması — Gemini, toplu (batch) çağrı.

NEDEN VAR: Kelime filtresi tek başına çok gürültülü. "Türk" kelimesi
geçen her haber bildirime dönüşürse insan bir hafta sonra bildirimleri
kapatır. LLM burada HAKEM: her adaya 1-10 puan, kategori ve Türkçe tek
cümle özet veriyor. Eşiği geçmeyen Telegram'a hiç düşmüyor.

KOTA MANTIĞI: Ücretsiz Gemini kotası günlük istek SAYISINA bakar
(model başına ~1.500 istek/gün, ~15 istek/dakika). Her haberi ayrı
çağrıyla puanlamak günde binlerce istek demekti — kota bu yüzden
patlıyordu. Burada bir taramanın tüm adayları BATCH_SIZE'lık gruplar
halinde tek istekte puanlanıyor: günde ~100 istek, her kotaya sığar.

MODEL DÜŞÜŞÜ: Google model adlarını zaman zaman emekliye ayırıyor.
Bir model 404 dönerse liste sırayla aşağı iniyor, bot ölmüyor.
Elle seçmek istersen: GEMINI_MODEL secret'ını tanımla.
"""

import json
import os
import time

import requests

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Sırayla denenir; ilk çalışan kullanılır. Hepsi ücretsiz kotada.
MODEL_CHAIN = [
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]

BATCH_SIZE = 25       # tek istekte puanlanan en fazla aday (az istek = az kota)
SUMMARY_CHARS = 220   # LLM'e giden özet uzunluğu (girdi token tasarrufu)
MAX_RETRY = 3         # 429 / 5xx için deneme sayısı
BASE_WAIT_S = 20      # ilk bekleme; her denemede iki katına çıkar

# TOKEN TASARRUFU NOTU: Bu başlık her batch'te gönderilir; o yüzden kısa ve
# yoğun tutuldu. Çıktı da JSON nesnesi değil kısa dizi: [nr,puan,kat,özet].
# Özet yalnızca 6+ puan alanlara istenir (çıktı token'ının büyük kısmı özet).
PROMPT_HEADER = """Rol: Münih'teki Türkçe konuşan toplum için Instagram haber editörü.
Her içeriğe 1-10 puan ver. Ölçüt: "Münih'te yaşayan bir Türk bunu bilmezse
zarar görür mü / paylaşmak ister mi?"

9-10: oturum-vatandaşlık-vize mevzuatı değişikliği; konsolosluk duyurusu;
 Münih/Bavyera'da Türk sanatçı, grup, komedi, talkshow, tiyatro etkinliği;
 resmi afet/güvenlik uyarısı; Türk toplumunu doğrudan etkileyen yerel karar.
6-8: Almanya'da göçmenleri etkileyen yasa/karar (çifte vatandaşlık, aile
 birleşimi, çalışma izni, sınır dışı, Bürgergeld/Kindergeld, vergi, kira,
 asgari ücret, sağlık sigortası, denklik, Deutschlandticket); hükümet krizi,
 seçim, koalisyon; Türk derneği/cami etkinliği, ramazan-bayram; Münih'te
 herkesi etkileyen aksaklık (MVG/S-Bahn grev-arıza, havalimanı, fırtına);
 şehri saran dev etkinlik (stadyum konseri, büyük festival).
 Türkiye kaynaklı haberde SADECE: tarihi çapta olay (büyük deprem, seçim
 sonucu, darbe, milli takım büyük başarı) veya Almanya'daki Türkleri
 doğrudan etkileyen gelişme (vize, gurbetçi, çifte vatandaşlık, bedelli,
 yurt dışı seçmen, THY, Türkiye-Almanya ilişkileri).
3-5: dolaylı — Türkiye iç gündemi, genel Alman siyaseti, orta boy yerel haber.
1-2: ilgisiz — kelime tesadüfen geçmiş, reklam, rutin spor, magazin.
Aynı olayın kopyalarında sadece EN İYİ olana yüksek puan ver, diğerleri 2.

İçerikler:
"""

PROMPT_FOOTER = """
Yalnızca JSON dizisi döndür, her içerik için bir eleman, sırayla:
[[nr, puan, kategori, özet], ...]
kategori: konser|etkinlik|resmi|almanya|turkiye|uyari|haber
özet: puan>=6 ise Türkçe en fazla 18 kelime, haberin özü (başlığı tekrarlama);
puan<6 ise boş string "". Eleman sayısı içerik sayısına eşit olmalı."""


class NoApiKey(Exception):
    """GEMINI_API_KEY tanımlı değil — main kelime moduna düşer."""


class QuotaExhausted(Exception):
    """Günlük/dakikalık kota bitti — kalan adaylar ertelenir."""


def available() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY"))


def _post(model: str, prompt: str) -> requests.Response:
    return requests.post(
        f"{API_BASE}/{model}:generateContent",
        params={"key": os.environ["GEMINI_API_KEY"]},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 4096,
                "response_mime_type": "application/json",
            },
        },
        timeout=90,
    )


def _call_gemini(prompt: str, state: dict) -> str:
    """Tek API çağrısı. state["model"] çalışan modeli hatırlar."""
    if not os.environ.get("GEMINI_API_KEY"):
        raise NoApiKey()

    models = [state["model"]] if state.get("model") else list(MODEL_CHAIN)
    for model in models:
        wait = BASE_WAIT_S
        for attempt in range(1, MAX_RETRY + 1):
            try:
                resp = _post(model, prompt)
            except requests.RequestException as e:
                print(f"[LLM] ağ hatası ({e.__class__.__name__}), {wait} sn bekleniyor")
                time.sleep(wait)
                wait *= 2
                continue

            if resp.status_code == 200:
                state["model"] = model
                data = resp.json()
                parts = (data.get("candidates") or [{}])[0] \
                    .get("content", {}).get("parts") or [{}]
                return parts[0].get("text", "")

            if resp.status_code in (404, 400) and state.get("model") is None:
                print(f"[LLM] model '{model}' kullanılamıyor "
                      f"(HTTP {resp.status_code}), sıradakine geçiliyor")
                break  # bir sonraki modeli dene

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRY:
                    raise QuotaExhausted()
                retry_after = int(resp.headers.get("Retry-After") or 0)
                sleep_s = max(retry_after, wait)
                print(f"[LLM] HTTP {resp.status_code} — {sleep_s} sn bekleyip "
                      f"tekrar deneniyor ({attempt}/{MAX_RETRY})")
                time.sleep(sleep_s)
                wait *= 2
                continue

            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")

    raise RuntimeError("Hiçbir Gemini modeli yanıt vermedi")


def _batch_prompt(batch: list[dict]) -> str:
    lines = []
    for i, it in enumerate(batch, 1):
        summary = it["summary"][:SUMMARY_CHARS]
        # Başlık özetin içinde tekrar ediyorsa özeti gönderme (token tasarrufu)
        if summary and summary.lower().startswith(it["title"].lower()[:40]):
            summary = summary[len(it["title"]):].strip(" -—:")
        line = f"{i}. [{it['source']}] {it['title']}"
        if summary:
            line += f" — {summary}"
        lines.append(line)
    return PROMPT_HEADER + "\n".join(lines) + PROMPT_FOOTER


VALID_KATEGORI = {"konser", "etkinlik", "resmi", "almanya", "turkiye", "uyari", "haber"}


def _parse_batch(text: str, batch_len: int) -> dict[int, dict]:
    """LLM cevabını {nr: sonuç} sözlüğüne çevirir; bozuk elemanı atlar.
    Hem kısa dizi biçimini [[nr,puan,kat,özet]] hem de eski nesne biçimini
    {"nr":..} kabul eder — model bazen talimata rağmen nesne döndürür."""
    text = (text or "").strip()
    if text.startswith("```"):  # model bazen markdown çitiyle sarıyor
        text = text.strip("`")
        text = text.split("\n", 1)[-1] if text[:4].lower() == "json" else text
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        text = text[start:end + 1]

    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("sonuclar") or data.get("results") or [data]

    results = {}
    for entry in data:
        try:
            if isinstance(entry, list):
                nr, puan = int(entry[0]), int(entry[1])
                kategori = str(entry[2]) if len(entry) > 2 else "haber"
                ozet = str(entry[3]) if len(entry) > 3 else ""
            else:
                nr, puan = int(entry["nr"]), int(entry.get("puan", 0))
                kategori = str(entry.get("kategori", "haber"))
                ozet = str(entry.get("ozet", ""))
            if not (1 <= nr <= batch_len):
                continue
            kategori = kategori.strip().lower()
            results[nr] = {
                "puan": max(1, min(10, puan)),
                "kategori": kategori if kategori in VALID_KATEGORI else "haber",
                "ozet": ozet.strip()[:300],
            }
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return results


def classify_all(items: list[dict], min_score: int = 6) -> dict:
    """Adayları toplu puanlar.

    Döner sözlük:
      winners    : eşiği geçenler (puan/kategori/ozet eklenmiş)
      evaluated  : gerçekten değerlendirilenler (seen'e yazılacaklar)
      deferred   : kota yüzünden ertelenenler (seen'e YAZILMAZ)
      failed     : kota dışı nedenle puanlanamayanlar
      model      : kullanılan model adı
    """
    winners, evaluated, failed = [], [], []
    state: dict = {"model": os.environ.get("GEMINI_MODEL") or None}

    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        try:
            results = _parse_batch(_call_gemini(_batch_prompt(batch), state),
                                   len(batch))
        except QuotaExhausted:
            deferred = items[start:]
            print(f"[LLM] Kota doldu — {len(deferred)} aday sonraki taramaya "
                  "ertelendi (seen'e yazılmıyor)")
            return {"winners": winners, "evaluated": evaluated,
                    "deferred": deferred, "failed": failed,
                    "model": state.get("model")}
        except Exception as e:
            print(f"[LLM HATA] batch {start // BATCH_SIZE + 1}: {e}")
            failed.extend(batch)
            evaluated.extend(batch)  # sonsuz döngüye girmesinler
            continue

        for i, it in enumerate(batch, 1):
            evaluated.append(it)
            result = results.get(i)
            if result is None:
                failed.append(it)
                continue
            print(f"[LLM] {result['puan']:>2} | {result['kategori']:<9} | "
                  f"{it['title'][:60]}")
            if result["puan"] >= min_score:
                winners.append({**it, **result})

        time.sleep(4)  # dakikalık istek limitine yaslanmamak için

    return {"winners": winners, "evaluated": evaluated, "deferred": [],
            "failed": failed, "model": state.get("model")}
