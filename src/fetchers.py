"""Kaynaklardan ham içerik çekme (RSS + HTML)."""

import feedparser
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MuenchenRadar/0.1; kisisel proje)"
}
TIMEOUT = 20


def fetch_rss(source: dict) -> list[dict]:
    """Bir RSS feed'ini okuyup standart item listesi döner."""
    items = []
    feed = feedparser.parse(source["url"], request_headers=HEADERS)
    for entry in feed.entries[:40]:  # feed başına üst sınır
        items.append({
            "source": source["name"],
            "title": (entry.get("title") or "").strip(),
            "summary": (entry.get("summary") or entry.get("description") or "").strip(),
            "link": (entry.get("link") or "").strip(),
            "trusted": source.get("trusted", False),
            "keyword_list": source.get("keyword_list", "default"),
        })
    return items


def fetch_html(source: dict) -> list[dict]:
    """Basit HTML kaynağı: verilen selector'daki linkleri item olarak toplar.

    Bu bilinçli olarak 'aptal' bir scraper — her site için özel parser yazmak
    yerine link + link metni topluyoruz; ilgili mi kararını LLM veriyor.
    """
    items = []
    resp = requests.get(source["url"], headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    must_contain = source.get("link_must_contain", "")
    best: dict[str, str] = {}  # link -> o linke işaret eden EN UZUN yazı
    order: list[str] = []
    for a in soup.select(source.get("item_selector", "a")):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or len(text) < 15:
            continue  # boş / çok kısa linkler gürültüdür
        if must_contain and must_contain not in href.lower():
            continue
        if href.startswith("/"):  # göreli linkleri mutlaklaştır
            base = source["url"].split("/", 3)
            href = f"{base[0]}//{base[2]}{href}"
        if href not in best:
            order.append(href)
            best[href] = text
        elif len(text) > len(best[href]):
            best[href] = text  # "Detayları gör" değil gerçek başlık kalsın
    for href in order[:40]:
        items.append({
            "source": source["name"],
            "title": best[href][:200],
            "summary": "",
            "link": href,
            "trusted": source.get("trusted", False),
            "keyword_list": source.get("keyword_list", "default"),
        })
    return items


def fetch_source(source: dict) -> list[dict]:
    """Tek bir kaynağı türüne göre çeker; hata olursa boş liste döner."""
    if not source.get("enabled", True):
        return []
    try:
        if source["type"] == "rss":
            return fetch_rss(source)
        if source["type"] == "html":
            return fetch_html(source)
    except Exception as e:  # tek kaynak çökerse tüm tarama çökmesin
        print(f"[HATA] {source['name']}: {e}")
    return []


def fetch_all(sources: list[dict]) -> list[dict]:
    items = []
    for src in sources:
        got = fetch_source(src)
        print(f"[FETCH] {src['name']}: {len(got)} item")
        items.extend(got)
    return items
