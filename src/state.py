"""Görülen içeriklerin kaydı (dedup).

Aynı haberi her taramada tekrar göndermemek için link hash'lerini
data/seen.json'da SIRALI liste olarak tutuyoruz — böylece dosya
dolduğunda en ESKİ kayıtlar atılır (rastgele değil) ve hâlâ feed'de
duran güncel içerikler yanlışlıkla "görülmemiş" sayılmaz.
"""

import hashlib
import json
from pathlib import Path

STATE_FILE = Path("data/seen.json")
MAX_ENTRIES = 5000  # dosya sonsuza kadar büyümesin


def item_id(item: dict) -> str:
    return hashlib.sha256(item["link"].encode()).hexdigest()[:16]


def load() -> list[str]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return []


def save(seen: list[str]) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen[-MAX_ENTRIES:], indent=0))


def filter_new(items: list[dict], seen: list[str]) -> list[dict]:
    known = set(seen)
    new_items = [it for it in items if item_id(it) not in known]
    print(f"[DEDUP] {len(items)} -> {len(new_items)} yeni item")
    return new_items


def mark_seen(seen: list[str], items: list[dict]) -> None:
    known = set(seen)
    for it in items:
        iid = item_id(it)
        if iid not in known:
            seen.append(iid)
            known.add(iid)
