"""Ucuz keyword prefilter — LLM'e giden hacmi küçültür.

Özellikler:
- Kelime sınırlı eşleşme: "mero" kelimesi "Kamerost" gibi kelimelerin
  içinde YANLIŞLIKLA eşleşmez, sadece bağımsız kelime olarak eşleşir.
- Kaynak bazlı kelime grubu: her kaynak kendi listesiyle filtrelenir
  (yerel medya -> "default", Türk ulusal medya -> "turkey_major").
- trusted=True kaynaklardan gelen her şey filtreyi otomatik geçer.
"""

import re


def _compile(keywords: list[str]) -> re.Pattern:
    parts = [rf"(?<!\w){re.escape(kw.lower())}(?!\w)" for kw in keywords]
    return re.compile("|".join(parts))


def compile_groups(keyword_groups: dict[str, list[str]]) -> dict[str, re.Pattern]:
    return {name: _compile(kws) for name, kws in keyword_groups.items() if kws}


def passes(item: dict, patterns: dict[str, re.Pattern]) -> bool:
    if item.get("trusted"):
        return True
    pattern = patterns.get(item.get("keyword_list", "default"))
    if pattern is None:  # tanımsız grup adı yazılmışsa default'a düş
        pattern = patterns.get("default")
    if pattern is None:
        return False
    text = f"{item['title']} {item['summary']}".lower()
    return bool(pattern.search(text))


def apply(items: list[dict], keyword_groups: dict[str, list[str]]) -> list[dict]:
    patterns = compile_groups(keyword_groups)
    kept = [it for it in items if passes(it, patterns)]
    print(f"[PREFILTER] {len(items)} -> {len(kept)} item")
    return kept
