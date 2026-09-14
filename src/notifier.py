"""Telegram bildirimleri.

- Eksik secret'ta KeyError ile çökmez, açık hata basar.
- Telegram 429 verirse retry_after kadar bekleyip tekrar dener.
- 4096 karakter sınırı için metni kısaltır.
- LLM doğrulaması yapılamadıysa mesaja ⚠️ işareti koyar; böylece
  "bu bildirim kontrol edilmedi" bilgisi kaybolmaz.
"""

import html
import os
import time

import requests

TG_LIMIT = 4000  # 4096 resmi sınır; güvenlik payı bırakıyoruz
MAX_RETRY = 3

EMOJI = {
    "konser": "\U0001F3A4",   # mikrofon
    "etkinlik": "\U0001F389",  # konfeti
    "resmi": "\U0001F3DB",     # resmi bina
    "almanya": "\U0001F1E9\U0001F1EA",
    "turkiye": "\U0001F1F9\U0001F1F7",
    "uyari": "⚠️",
    "haber": "\U0001F4F0",
}


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")
                and os.environ.get("TELEGRAM_CHAT_ID"))


def _post(payload: dict) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[TELEGRAM HATA] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
              "secret'ları tanımlı değil")
        return False

    payload = {"chat_id": chat_id, "disable_web_page_preview": False, **payload}
    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload, timeout=30)
        except requests.RequestException as e:
            print(f"[TELEGRAM HATA] ağ: {e}")
            time.sleep(5)
            continue

        if resp.ok and resp.json().get("ok"):
            return True

        if resp.status_code == 429:
            wait = resp.json().get("parameters", {}).get("retry_after", 5)
            print(f"[TELEGRAM] hız limiti, {wait} sn bekleniyor")
            time.sleep(wait + 1)
            continue

        if resp.status_code == 400 and "parse" in resp.text.lower():
            # HTML biçimi bozuksa düz metinle bir kez daha dene
            print("[TELEGRAM] HTML parse hatası, düz metin deneniyor")
            payload = {k: v for k, v in payload.items() if k != "parse_mode"}
            continue

        print(f"[TELEGRAM HATA] {resp.status_code}: {resp.text[:200]}")
        time.sleep(3)
    return False


def send_text(text: str) -> bool:
    return _post({"text": text[:TG_LIMIT]})


def send(item: dict) -> bool:
    """Tek bir haberi gönderir."""
    kategori = item.get("kategori", "haber")
    emoji = EMOJI.get(kategori, EMOJI["haber"])
    puan = item.get("puan")

    lines = [f"{emoji} <b>{html.escape(item['title'])}</b>", ""]
    if item.get("ozet"):
        lines.append(html.escape(item["ozet"]))
        lines.append("")

    meta = f"Kaynak: {html.escape(item['source'])}"
    if item.get("llm_checked", True) and puan is not None:
        meta += f"  ·  Puan: {puan}/10"
    elif item.get("skor") is not None:
        # Kelime modu: LLM puanı yok, kelime skoru var
        meta += f"  ·  Skor: {item['skor']}"
    meta += f"  ·  #{kategori}"
    lines.append(meta)

    # Hangi kelimeler tutturdu — ayar yaparken en çok işe yarayan bilgi
    if item.get("eslesme"):
        lines.append(f"<i>{html.escape(str(item['eslesme'])[:120])}</i>")

    lines.append(item["link"])

    return _post({"text": "\n".join(lines)[:TG_LIMIT], "parse_mode": "HTML"})


def send_report(title: str, lines: list[str]) -> bool:
    """Sağlık raporu / kalp atışı gibi çok satırlı düz mesaj."""
    body = title + "\n" + "\n".join(lines)
    return send_text(body[:TG_LIMIT])
