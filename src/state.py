"""Hafıza: görülen içerikler, kaynak sağlığı, ilk tur (bootstrap) kaydı.

data/seen.json     -> gönderilmiş/değerlendirilmiş link hash'leri, SIRALI.
                      Sıralı olduğu için dosya dolunca en ESKİ kayıt atılır.
data/kaynaklar.json-> kaynak başına: üst üste kaç tur boş döndü,
                      ilk turu yapıldı mı.

İLK TUR (bootstrap) NEDEN VAR: Konsolosluk sayfası, mekan sayfaları ve
dernek siteleri tarih vermiyor. Bir kaynak ilk kez tarandığında sayfada
duran her şey "yeni" görünür — 3 yıllık duyurular dahil. O yüzden bir
kaynağın İLK turunda bulunan her şey seen'e yazılır ama GÖNDERİLMEZ.
O andan sonra eklenen her yeni içerik normal akışa girer.
"""

import hashlib
import json
from pathlib import Path

DATA_DIR = Path("data")
STATE_FILE = DATA_DIR / "seen.json"
KAYNAK_FILE = DATA_DIR / "kaynaklar.json"
MAX_ENTRIES = 12000
OLU_SAYILIR = 6  # bu kadar tur üst üste boş dönen kaynak "ölü" sayılır


def item_id(item: dict) -> str:
    return hashlib.sha256(item["link"].encode()).hexdigest()[:16]


# ------------------------------------------------------------------- seen
def load() -> list[str]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            print("[HAFIZA] seen.json bozuk, sıfırdan başlanıyor")
    return []


def save(seen: list[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(seen[-MAX_ENTRIES:], indent=0))


def filter_new(items: list[dict], seen: list[str]) -> list[dict]:
    bilinen = set(seen)
    yeni = [it for it in items if item_id(it) not in bilinen]
    print(f"[HAFIZA] {len(items)} -> {len(yeni)} yeni item")
    return yeni


def mark_seen(seen: list[str], items: list[dict]) -> None:
    bilinen = set(seen)
    for it in items:
        iid = item_id(it)
        if iid not in bilinen:
            seen.append(iid)
            bilinen.add(iid)


# -------------------------------------------------------------- kaynaklar
def _kaynaklari_oku() -> dict:
    try:
        return json.loads(KAYNAK_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _kaynaklari_yaz(veri: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    KAYNAK_FILE.write_text(json.dumps(veri, indent=1, ensure_ascii=False))


def ilk_turu_bekleyenler(kaynak_adlari: list[str]) -> set[str]:
    """Daha önce hiç taranmamış kaynakların adlarını döner."""
    veri = _kaynaklari_oku()
    return {ad for ad in kaynak_adlari if not veri.get(ad, {}).get("ilk_tur")}


def ilk_turu_isaretle(kaynak_adlari) -> None:
    veri = _kaynaklari_oku()
    for ad in kaynak_adlari:
        veri.setdefault(ad, {})["ilk_tur"] = True
    _kaynaklari_yaz(veri)


def saglik_guncelle(saglik: list[tuple[str, str]]) -> tuple[list[str], int]:
    """Kaynak sağlığını günceller.

    Döner: (yeni ölen kaynakların listesi, o turdaki hatalı kaynak sayısı).
    """
    veri = _kaynaklari_oku()
    yeni_olenler = []
    hatali = 0

    for ad, durum in saglik:
        if durum == "kapalı":
            continue
        kayit = veri.setdefault(ad, {})
        calisti = not durum.startswith("HATA")
        if not calisti:
            hatali += 1
        onceki = kayit.get("bos", 0)
        kayit["bos"] = 0 if calisti else onceki + 1
        kayit["son_durum"] = durum[:120]
        if kayit["bos"] == OLU_SAYILIR:
            yeni_olenler.append(f"{ad} — {durum[:80]}")

    _kaynaklari_yaz(veri)
    return yeni_olenler, hatali
