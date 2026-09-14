"""Kaynaklardan ham içerik çekme (RSS + HTML + JSON API).

Tasarım kuralları:
- HER kaynak kendi hatasıyla ölür, tarama devam eder.
- HER istek timeout'lu. feedparser'a URL VERİLMEZ (timeout'u yoktur,
  GitHub Actions run'ını saatlerce asabilir) — önce requests ile
  indirilir, sonra byte'lar parse edilir.
- Bir kaynağın `url_fallback` listesi varsa, ana URL ölürse sırayla
  denenir. Feed adresleri yıllar içinde değişir; bu sayede bot
  kendi kendini onarır.
- fetch_all ayrıca "sağlık raporu" döndürür: hangi kaynak kaç item
  verdi, hangisi patladı. main bunu Telegram'a düşürebilir.
"""

import json
import urllib.robotparser
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

# YASAL ÇERÇEVE — bu bot yalnızca:
#   * sitelerin kendi yayınladığı RSS beslemelerini (abone olunması için var),
#   * resmi/açık JSON API'leri (MVG, NINA, Ticketmaster — hepsi anahtarlı
#     veya herkese açık),
#   * herkese açık HTML sayfalarını, robots.txt'e UYARAK ve kendini dürüstçe
#     tanıtan bir User-Agent ile
# okur. Kimliğini gizlemez, giriş gerektiren hiçbir yere girmez, içerik
# kopyalamaz — sadece başlık + link toplar ve kaynağa yönlendirir.
HEADERS = {
    "User-Agent": ("BizimMunihRadar/2.0 (+https://github.com/mandalina22/sultan; "
                   "kisisel, ticari olmayan haber radari)"),
    "Accept": "application/rss+xml, application/xml, text/xml, application/json, text/html;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,tr;q=0.8,en;q=0.7",
}
TIMEOUT = 25
MAX_PER_SOURCE = 40

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def robots_allows(url: str) -> bool:
    """HTML sayfaları için robots.txt kontrolü. robots.txt okunamazsa
    (yok / hata) izin var sayılır — standart davranış budur."""
    parts = urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = requests.get(f"{base}/robots.txt", headers=HEADERS, timeout=10)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                _robots_cache[base] = rp
            else:
                _robots_cache[base] = None
        except requests.RequestException:
            _robots_cache[base] = None
    rp = _robots_cache[base]
    if rp is None:
        return True
    return rp.can_fetch(HEADERS["User-Agent"], url) and rp.can_fetch("*", url)


class RobotsDisallowed(Exception):
    pass


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    resp.raise_for_status()
    return resp


def _decode(resp: requests.Response) -> str:
    """Sunucu charset bildirmezse requests ISO-8859-1 varsayar ve Türkçe
    karakterler bozulur ("Vatandaşlık" -> "VatandaÅlÄ±k"). Konsolosluk
    sayfası tam olarak bu tuzağa düşüyordu. Önce UTF-8 deneriz."""
    declared = (resp.headers.get("content-type") or "").lower()
    if "charset=" in declared:
        return resp.text
    try:
        return resp.content.decode("utf-8")
    except UnicodeDecodeError:
        return resp.content.decode(resp.apparent_encoding or "latin-1",
                                   errors="replace")


def _item(source: dict, title: str, summary: str, link: str) -> dict:
    return {
        "source": source["name"],
        "title": (title or "").strip()[:300],
        "summary": (summary or "").strip()[:1200],
        "link": (link or "").strip(),
        # trusted: kelime filtresini atlar, LLM'e gider
        # direct : kelime filtresini DE LLM'i DE atlar, olduğu gibi gönderilir
        #          (zaten Türkçe olan resmi kaynaklar için — API tasarrufu)
        "trusted": source.get("trusted", False) or source.get("direct", False),
        "direct": source.get("direct", False),
        "keyword_list": source.get("keyword_list", "default"),
    }


def _strip_html(raw: str) -> str:
    if not raw or "<" not in raw:
        return raw or ""
    return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)


# --------------------------------------------------------------------- RSS
def fetch_rss(source: dict, url: str) -> list[dict]:
    resp = _get(url)
    feed = feedparser.parse(resp.content)
    items = []
    for entry in feed.entries[:MAX_PER_SOURCE]:
        summary = entry.get("summary") or entry.get("description") or ""
        items.append(_item(source, entry.get("title", ""),
                           _strip_html(summary), entry.get("link", "")))
    return items


# -------------------------------------------------------------------- HTML
def _absolute(href: str, page_url: str) -> str:
    """Göreli linki mutlak hale getirir (//, /, ve düz göreli dahil)."""
    if href.startswith(("http://", "https://")):
        return href
    parts = page_url.split("/")
    scheme, host = parts[0], parts[2]
    if href.startswith("//"):
        return f"{scheme}{href}"
    if href.startswith("/"):
        return f"{scheme}//{host}{href}"
    base = page_url.rsplit("/", 1)[0]
    return f"{base}/{href}"


def fetch_html(source: dict, url: str) -> list[dict]:
    """Basit link toplayıcı. Her site için özel parser yazmak yerine
    link + link metni topluyoruz; ilgili mi kararını LLM veriyor."""
    if not robots_allows(url):
        raise RobotsDisallowed("robots.txt bu sayfayı botlara kapatmış")
    resp = _get(url)
    soup = BeautifulSoup(_decode(resp), "html.parser")

    must = (source.get("link_must_contain") or "").lower()
    min_len = source.get("min_text_len", 15)
    best: dict[str, str] = {}
    order: list[str] = []

    for a in soup.select(source.get("item_selector", "a")):
        href = (a.get("href") or "").strip()
        text = a.get_text(" ", strip=True)
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        if len(text) < min_len:
            continue
        if must and must not in href.lower():
            continue
        href = _absolute(href, url)
        if href not in best:
            order.append(href)
            best[href] = text
        elif len(text) > len(best[href]):
            best[href] = text  # "Detayları gör" değil gerçek başlık kalsın

    return [_item(source, best[h], "", h) for h in order[:MAX_PER_SOURCE]]


# -------------------------------------------------------------------- JSON
def parse_mvg(source: dict, data) -> list[dict]:
    """MVG Betriebsmeldungen — www.mvg.de/api/bgw-pt/v3/messages"""
    items = []
    for msg in (data if isinstance(data, list) else [])[:MAX_PER_SOURCE]:
        title = msg.get("title") or ""
        desc = _strip_html(msg.get("description") or "")
        lines = ", ".join(
            ln.get("label", "") for ln in (msg.get("lines") or [])[:12])
        if lines:
            title = f"{title} ({lines})"
        link = ""
        for lk in msg.get("links") or []:
            if lk.get("url"):
                link = lk["url"]
                break
        if not link:
            link = f"https://www.mvg.de/verbindungen/betriebsmeldungen.html#{msg.get('publication', '')}"
        items.append(_item(source, f"MVG: {title}", desc, link))
    return items


def parse_nina(source: dict, data) -> list[dict]:
    """NINA / DWD resmi uyarılar — warnung.bund.de/api31/dashboard/<ARS>.json

    i18nTitle içinde Türkçe çeviri geliyor; varsa onu da ekliyoruz."""
    items = []
    for alert in (data if isinstance(data, list) else [])[:MAX_PER_SOURCE]:
        payload = (alert.get("payload") or {}).get("data") or {}
        if payload.get("msgType") == "Cancel":
            continue
        headline = payload.get("headline") or alert.get("id", "")
        tr = (alert.get("i18nTitle") or {}).get("TR") or ""
        severity = payload.get("severity", "")
        summary = f"Önem: {severity}. {tr}".strip()
        items.append(_item(
            source,
            f"UYARI: {headline}",
            summary,
            f"https://warnung.bund.de/meldungen/{alert.get('id', '')}",
        ))
    return items


JSON_PARSERS = {"mvg": parse_mvg, "nina": parse_nina}


def fetch_json(source: dict, url: str) -> list[dict]:
    resp = _get(url)
    data = json.loads(resp.content)
    parser = JSON_PARSERS.get(source.get("parser", ""))
    if parser is None:
        raise ValueError(f"bilinmeyen json parser: {source.get('parser')}")
    return parser(source, data)


# ------------------------------------------------------------------ toplam
FETCHERS = {"rss": fetch_rss, "html": fetch_html, "json": fetch_json}


def fetch_source(source: dict) -> tuple[list[dict], str]:
    """Tek kaynak çeker. Döner: (item listesi, durum metni)."""
    if not source.get("enabled", True):
        return [], "kapalı"

    fetcher = FETCHERS.get(source.get("type", "rss"))
    if fetcher is None:
        return [], f"HATA: bilinmeyen tip '{source.get('type')}'"

    urls = [source["url"]] + list(source.get("url_fallback") or [])
    last_error = ""
    for i, url in enumerate(urls):
        try:
            items = fetcher(source, url)
        except Exception as e:
            last_error = f"{type(e).__name__}: {str(e)[:120]}"
            continue
        if items:
            note = f"{len(items)} item"
            if i > 0:
                note += f" (yedek URL #{i} ile)"
            return items, note
        last_error = "0 item döndü"
    return [], f"HATA: {last_error}"


def fetch_all(sources: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Döner: (tüm item'lar, [(kaynak adı, durum), ...])"""
    items: list[dict] = []
    health: list[tuple[str, str]] = []
    for src in sources:
        got, status = fetch_source(src)
        print(f"[FETCH] {src['name']}: {status}")
        health.append((src["name"], status))
        items.extend(got)
    return items, health
