"""Kelime ön filtresi — LLM'e giden hacmi küçültür.

TASARIM: Burada AMAÇ geniş davranmak (recall). Yanlış pozitifleri
LLM eliyor; ama burada elenen bir haber bir daha geri gelmez.
Eski sürümdeki en büyük hata buydu: tam kelime eşleşmesi kullanılıyordu,
yani "türk" kelimesi "Türken", "türkische", "türkischstämmige"
kelimelerinde EŞLEŞMİYORDU. Almanca çekim ve birleşik kelime dili
olduğu için bu, haberlerin büyük kısmını sessizce kaçırıyordu.

Eşleşme kuralları (kelimenin başındaki işarete göre):
  "=kelime"  -> TAM kelime eşleşmesi.
  "~kelime"  -> TAM kelime eşleşmesi + metinde bir "sahne bağlamı" kelimesi
                de olmalı (konzert, tour, tickets, konser, sahne...).
                Türkçede günlük anlamı olan sanatçı adları için:
                "~duman", "~ceza", "~elif", "~sıla". Böylece "dumanlı hava"
                haberi LLM'e gitmez, "Duman konseri" gider. Her boşa giden
                aday = boşa giden API token'ı.
  6+ harfli  -> düz alt-dize. "einbürgerung" -> "Einbürgerungstest".
  daha kısa  -> kelime başı + ek serbest. "türk" -> "Türken", "türkisch".
"""

import re

# Python'da "İ".lower() -> "i" + U+0307 (birleşik nokta) verir, düz "i"
# vermez. Türkçe isimler bu yüzden sessizce eşleşmeyebiliyor.
COMBINING_DOT = "̇"

# "~" işaretli kelimeler için gereken bağlam. Almanca + Türkçe + İngilizce.
CONTEXT_WORDS = (
    "konzert", "konser", "concert", "live", "tour", "tournee", "turne",
    "ticket", "bilet", "show", "auftritt", "bühne", "sahne", "gala",
    "festival", "gecesi", "album", "albüm", "single", "şarkı", "song",
    "sänger", "sängerin", "musiker", "rapper", "band", "grup", "dj",
    "comedy", "stand-up", "standup", "tiyatro", "theater", "kabarett",
    "halle", "arena", "stadion", "olympia", "zenith", "muffat", "backstage",
    "circus krone", "tonhalle", "kongresshalle",
)
_CONTEXT_RE = re.compile("|".join(re.escape(w) for w in CONTEXT_WORDS))


def _norm(text: str) -> str:
    return text.lower().replace(COMBINING_DOT, "")


def _pattern_for(keyword: str) -> str:
    kw = _norm(keyword.strip())
    if kw.startswith(("=", "~")):
        return rf"(?<!\w){re.escape(kw[1:])}(?!\w)"
    if len(kw) >= 6:
        return re.escape(kw)
    return rf"(?<!\w){re.escape(kw)}\w*"


def compile_groups(keyword_groups: dict[str, list[str]]) -> dict[str, dict]:
    """Her grup için iki regex: düz kelimeler ve bağlam isteyen kelimeler."""
    compiled = {}
    for name, keywords in keyword_groups.items():
        plain, contextual = [], []
        for k in keywords or []:
            if not k or not k.strip():
                continue
            (contextual if k.strip().startswith("~") else plain).append(k)
        if not plain and not contextual:
            continue  # boş liste "her şey eşleşir" demek olurdu — tehlikeli
        compiled[name] = {
            "plain": re.compile("|".join(_pattern_for(k) for k in plain)) if plain else None,
            "ctx": re.compile("|".join(_pattern_for(k) for k in contextual)) if contextual else None,
        }
    return compiled


def match_reason(item: dict, patterns: dict[str, dict]) -> str | None:
    """Eşleşen kelimeyi döner; eşleşme yoksa None."""
    if item.get("trusted"):
        return "güvenilir kaynak"
    group = patterns.get(item.get("keyword_list", "default")) \
        or patterns.get("default")
    if group is None:
        return None
    text = _norm(f"{item.get('title', '')} {item.get('summary', '')}")

    if group["plain"] is not None:
        hit = group["plain"].search(text)
        if hit:
            return hit.group(0)
    if group["ctx"] is not None:
        hit = group["ctx"].search(text)
        if hit and _CONTEXT_RE.search(text):
            return f"{hit.group(0)} (+bağlam)"
    return None


def apply(items: list[dict], keyword_groups: dict[str, list[str]]) -> list[dict]:
    patterns = compile_groups(keyword_groups)
    kept = []
    for it in items:
        reason = match_reason(it, patterns)
        if reason:
            kept.append({**it, "match": reason})
    print(f"[PREFILTER] {len(items)} -> {len(kept)} item")
    return kept
