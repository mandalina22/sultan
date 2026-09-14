"""Ağırlıklı aday puanlama — eski prefilter.py'nin yerini aldı.

NEDEN DEĞİŞTİ: Eski sistem "bir kelime eşleşti mi aday" diyordu.
"Türkiye" geçen her haber aday oluyordu, "Bayern kazandı" bile.
Şimdi her kelimenin ağırlığı var, puanlar toplanıyor, eşiği geçen
aday oluyor. Aday belirleme burada biter — başka bir aşama yok.

Puanlar (config/keywords.yml):
  kesin   -> tek başına yeter
  guclu   -> 2
  baglam  -> 1
  negatif -> -3
Başlıkta geçen kelime 2 katı sayılır.

KAYNAK DA ÖNEMLİ: aynı kelime her yerde aynı şeyi ifade etmiyor.
"Türkei" Tagesschau dış haberlerinde tek başına yeterken (grup
sözlüğünde "kesin"), yerel gazetede 2 puanlık sıradan bir kelime.
Ayrıca kaynağın sources.yml'deki "otorite" değeri puana eklenir.
"""

import re

PUAN = {"kesin": 100, "guclu": 2, "baglam": 1, "negatif": -3}
BASLIK_CARPANI = 2
VARSAYILAN_ESIK = 3
KESIN_PUAN = PUAN["kesin"]

# HARF NORMALİZASYONU — recall için kritik.
# Kaynaklar aynı kelimeyi üç türlü yazıyor:
#   "Einbürgerung" / "Einbuergerung"   (Almanca çift yazım)
#   "Gülşen" / "Gulsen"                (Türkçe karakter kullanmayan siteler)
# Çözüm: metin DÜZ ASCII'ye indirgenir; her kelime için hem düz ASCII
# hem de Almanca "ue/oe/ae" açılımı denenir. Böylece üç yazım da tutar.
# Ayrıca Python'da "İ".lower() düz "i" vermez, "i"+U+0307 verir — o
# birleşik nokta da burada siliniyor.
BIRLESIK_NOKTA = "̇"
ASCII_HARITA = str.maketrans({
    "ü": "u", "ö": "o", "ä": "a", "ğ": "g", "ş": "s", "ç": "c", "ı": "i",
    "â": "a", "î": "i", "û": "u", "é": "e", "è": "e", "à": "a",
})
ALMANCA_ACILIM = str.maketrans({"ü": "ue", "ö": "oe", "ä": "ae"})

# "~" işaretli kelimeler için gereken sahne bağlamı.
BAGLAM_KELIMELERI = (
    "konzert", "konser", "concert", "live", "tour", "tournee", "turne",
    "ticket", "bilet", "show", "auftritt", "bühne", "sahne", "gala",
    "festival", "gecesi", "album", "albüm", "single", "şarkı", "song",
    "sänger", "musiker", "rapper", "band", "grup", "comedy", "stand-up",
    "standup", "tiyatro", "theater", "kabarett", "halle", "arena",
    "stadion", "olympia", "muffat", "backstage", "circus krone", "tonhalle",
)
_BAGLAM_RE = re.compile("|".join(re.escape(w) for w in BAGLAM_KELIMELERI))


def _norm(text: str) -> str:
    """Metni karşılaştırma biçimine indirger: küçük harf + düz ASCII."""
    return ((text or "").lower()
            .replace(BIRLESIK_NOKTA, "")
            .replace("ß", "ss")
            .translate(ASCII_HARITA))


def _tek_desen(kw: str) -> str:
    """İşaret kurallarını uygular (kurallar dosya başında)."""
    if kw.startswith(("=", "~")):
        return rf"(?<!\w){re.escape(kw[1:])}(?!\w)"
    if len(kw) >= 6:
        return re.escape(kw)
    return rf"(?<!\w){re.escape(kw)}\w*"


def _desen(kelime: str) -> str:
    """Bir kelimeyi regex'e çevirir — iki yazım biçimini de kapsar."""
    ham = (kelime or "").strip().lower().replace(BIRLESIK_NOKTA, "")
    isaret = ham[0] if ham[:1] in ("=", "~") else ""
    govde = ham[len(isaret):]

    duz = _norm(govde)                                    # einburgerung
    almanca = _norm(govde.translate(ALMANCA_ACILIM))      # einbuergerung
    varyantlar = [duz] if almanca == duz else [duz, almanca]
    return "|".join(_tek_desen(isaret + v) for v in varyantlar if v)


def _derle(kelimeler: list[str]):
    """Döner: (düz regex, bağlam isteyen regex). Boşsa None."""
    duz = [k for k in kelimeler if k and not k.strip().startswith("~")]
    ctx = [k for k in kelimeler if k and k.strip().startswith("~")]
    return (
        re.compile("|".join(_desen(k) for k in duz)) if duz else None,
        re.compile("|".join(_desen(k) for k in ctx)) if ctx else None,
    )


def derle_gruplar(sozluk: dict) -> dict:
    """keywords.yml'yi derlenmiş gruplara çevirir.

    sozluk: {"ortak": {kesin: [...], ...}, "gruplar": {"yerel": {...}}}
    Döner: {grup_adı: {"esik": n, "katman": {kat: (duz, ctx)}}}
    """
    ortak = sozluk.get("ortak") or {}
    gruplar = sozluk.get("gruplar") or {}
    derlenmis = {}

    # "varsayilan" adında sanal bir grup: tanımsız grup adları buraya düşer
    for ad, ayar in list(gruplar.items()) + [("varsayilan", {})]:
        ayar = ayar or {}
        katman = {}
        for kat in ("kesin", "guclu", "baglam", "negatif"):
            kelimeler = list(ortak.get(kat) or []) + list(ayar.get(kat) or [])
            if kelimeler:
                katman[kat] = _derle(kelimeler)
        # zorunlu: KAPI. Bu listeden en az biri eşleşmezse item puanına
        # bakılmadan elenir. "Berlin'de Tarkan konseri" ne kadar puan
        # alırsa alsın Münih'li için haber değil — puanla çözülmez, kapıyla
        # çözülür. Ortak sözlükle birleşmez, sadece gruba aittir.
        zorunlu = list(ayar.get("zorunlu") or [])
        derlenmis[ad] = {
            "esik": int(ayar.get("esik", VARSAYILAN_ESIK)),
            "katman": katman,
            "zorunlu": _derle(zorunlu) if zorunlu else None,
        }
    return derlenmis


def _say(regexler, metin: str, baglam_var: bool) -> list[str]:
    """Metindeki eşleşmeleri döner (tekrarsız)."""
    duz, ctx = regexler
    bulunan = []
    if duz is not None:
        bulunan += duz.findall(metin) if duz.groups == 0 else [
            m.group(0) for m in duz.finditer(metin)]
    if ctx is not None and baglam_var:
        bulunan += [m.group(0) for m in ctx.finditer(metin)]
    # findall boş string döndürebilir; temizle ve tekrarları at
    temiz, gorulen = [], set()
    for b in bulunan:
        if b and b not in gorulen:
            gorulen.add(b)
            temiz.append(b)
    return temiz


def puanla(item: dict, gruplar: dict) -> dict:
    """Tek bir item'ı puanlar.

    Döner: {"puan": n, "esik": n, "aday": bool, "eslesme": [...], "kesin": bool}
    """
    grup = gruplar.get(item.get("keyword_list", "varsayilan")) \
        or gruplar.get("varsayilan")
    if grup is None:
        return {"puan": 0, "esik": 99, "aday": False, "eslesme": [], "kesin": False}

    baslik = _norm(item.get("title", ""))
    ozet = _norm(item.get("summary", ""))
    tumu = f"{baslik} {ozet}"
    baglam_var = bool(_BAGLAM_RE.search(tumu))

    # Kapı: zorunlu liste varsa, en az biri eşleşmeli
    if grup["zorunlu"] is not None and not _say(grup["zorunlu"], tumu, baglam_var):
        return {"puan": 0, "esik": grup["esik"], "aday": False,
                "eslesme": [], "kesin": False}

    puan = 0
    eslesme = []
    kesin_mi = False

    for kat, regexler in grup["katman"].items():
        carpan = PUAN[kat]
        basliktakiler = _say(regexler, baslik, baglam_var)
        ozettekiler = [e for e in _say(regexler, ozet, baglam_var)
                       if e not in basliktakiler]

        if kat == "kesin":
            if basliktakiler or ozettekiler:
                kesin_mi = True
                eslesme += [f"!{e}" for e in (basliktakiler + ozettekiler)[:3]]
            continue

        for e in basliktakiler:
            puan += carpan * BASLIK_CARPANI
            eslesme.append(f"{e}*")
        for e in ozettekiler:
            puan += carpan
            eslesme.append(e)

    # Kaynağın kendi ağırlığı: seçici yayın yapan kaynaklarda aynı
    # kelime daha anlamlıdır. Puan 0 ise ekleme (alakasız haberi
    # sırf kaynağı iyi diye yukarı çekmeyelim).
    if puan > 0:
        puan += int(item.get("otorite", 0) or 0)

    if kesin_mi:
        puan += KESIN_PUAN

    return {
        "puan": puan,
        "esik": grup["esik"],
        "aday": puan >= grup["esik"],
        "eslesme": eslesme[:8],
        "kesin": kesin_mi,
    }


def uygula(items: list[dict], sozluk: dict, yakin_sayisi: int = 12):
    """Hepsini puanlar.

    Döner: (adaylar, sınırda kalanlar).
    Sınırda kalanlar = eşiği geçemeyen ama en yüksek puanlı olanlar.
    Günlük "kaçan var mı" raporu için — kör ayar yapmamak adına.
    """
    gruplar = derle_gruplar(sozluk)
    adaylar, kalanlar = [], []
    for it in items:
        sonuc = puanla(it, gruplar)
        zenginlestirilmis = {**it, "skor": sonuc["puan"],
                             "eslesme": ", ".join(sonuc["eslesme"])}
        if it.get("trusted") or sonuc["aday"]:
            adaylar.append(zenginlestirilmis)
        elif sonuc["puan"] > 0:
            kalanlar.append(zenginlestirilmis)

    adaylar.sort(key=lambda x: x["skor"], reverse=True)
    kalanlar.sort(key=lambda x: x["skor"], reverse=True)
    print(f"[PUANLAMA] {len(items)} item -> {len(adaylar)} aday, "
          f"{len(kalanlar)} sınırda")
    return adaylar, kalanlar[:yakin_sayisi]
