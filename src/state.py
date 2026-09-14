"""Görülen içeriklerin kaydı (dedup) + kaynak sağlık geçmişi.

data/seen.json  -> gönderilmiş/değerlendirilmiş link hash'leri, SIRALI.
                   Sıralı olduğu için dosya dolunca en ESKİ kayıtlar atılır.
data/health.json-> her kaynağın üst üste kaç turdur 0 item verdiği.
                   Bir kaynak sessizce ölürse bot bunu fark edip haber verir.
"""

import hashlib
import json
from pathlib import Path

DATA_DIR = Path("data")
STATE_FILE = DATA_DIR / "seen.json"
HEALTH_FILE = DATA_DIR / "health.json"
MAX_ENTRIES = 8000
DEAD_AFTER = 6  # bu kadar tur üst üste boş dönen kaynak "ölü" sayılır


def item_id(item: dict) -> str:
    return hashlib.sha256(item["link"].encode()).hexdigest()[:16]


# ------------------------------------------------------------------- seen
def load() -> list[str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            print("[STATE] seen.json bozuk, sıfırdan başlanıyor")
    return []


def save(seen: list[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
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


# ----------------------------------------------------------------- health
def update_health(health: list[tuple[str, str]]) -> list[str]:
    """Kaynak sağlığını günceller ve YENİ ölen kaynakların adını döner."""
    try:
        counters = json.loads(HEALTH_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        counters = {}

    newly_dead = []
    for name, status in health:
        if status == "kapalı":
            continue
        ok = not status.startswith("HATA")
        before = counters.get(name, 0)
        counters[name] = 0 if ok else before + 1
        if counters[name] == DEAD_AFTER:
            newly_dead.append(f"{name} — {status}")

    DATA_DIR.mkdir(exist_ok=True)
    HEALTH_FILE.write_text(json.dumps(counters, indent=1, ensure_ascii=False))
    return newly_dead
