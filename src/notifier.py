"""Telegram'a bildirim gönderme (LLM'siz format: başlık + kaynak + link)."""

import html
import os

import requests


def _emoji(item: dict) -> str:
    source = item.get("source", "").lower()
    title = item.get("title", "")
    if "bandsintown" in source or "ticketmaster" in source or title.startswith("KONSER"):
        return "\U0001F3A4"  # mikrofon
    if "başkonsolos" in source or "konsolos" in source:
        return "\U0001F3DB"  # resmi bina
    return "\U0001F4F0"      # gazete


def send(item: dict) -> bool:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    text = (
        f"{_emoji(item)} <b>{html.escape(item['title'])}</b>\n\n"
        f"Kaynak: {html.escape(item['source'])}\n"
        f"{item['link']}"
    )

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        timeout=20,
    )
    ok = resp.ok and resp.json().get("ok", False)
    if not ok:
        print(f"[TELEGRAM HATA] {resp.status_code}: {resp.text[:200]}")
    return ok


def send_text(text: str) -> bool:
    """Düz metin mesajı gönderir (kalp atışı / test için)."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=20,
    )
    ok = resp.ok and resp.json().get("ok", False)
    if not ok:
        print(f"[TELEGRAM HATA] {resp.status_code}: {resp.text[:200]}")
    return ok
