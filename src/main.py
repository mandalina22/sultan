"""Münih Radar — ana akış.

HAT: kaynaklar -> tarih filtresi -> tarama içi dedup -> hafıza
     -> ilk tur sessizliği -> ağırlıklı puanlama -> (LLM) -> Telegram

TASARIM KARARLARI

1) Puanlama tek başına yeterli olmalı. Eskiden "bir kelime eşleşti mi
   aday" idi; "Türkiye" geçen her haber geçiyordu. Artık kelimelerin
   ağırlığı var (src/scoring.py). GEMINI_API_KEY olmadan da sistem
   düzgün çalışır; LLM varsa ikinci hakem olarak devreye girer.

2) Tarih: GEÇMİŞ tarihli bir haber en fazla MAX_YAS_SAAT kadar eski
   olabilir. GELECEK tarihli içerik (duyurulmuş etkinlik, 2036 bile
   olsa) hiçbir zaman elenmez.

3) İlk tur sessizliği: tarih vermeyen kaynaklarda (konsolosluk, mekan
   sayfaları) ilk taramada bulunan her şey hafızaya yazılır ama
   gönderilmez. Yoksa 3 yıllık duyurular bildirim olarak düşer.

4) Sessiz ölmesin: kaynakların üçte birinden fazlası hata verirse
   Telegram'a uyarı gider.

Çalıştırma:
    python -m src.main                  # haber taraması (saatte bir)
    python -m src.main --events         # etkinlik taraması (günde 2)
    python -m src.main --report         # + günlük rapor
    python -m src.main --test-sources   # sadece kaynakları test et
    python -m src.main --dry-run        # her şeyi yap, gönderme
    python -m src.main --ping           # Telegram bağlantı testi
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src import classifier, concerts, fetchers, notifier, scoring, state

SEND_SLEEP_S = 3        # Telegram hız limiti
MIN_SCORE = 6           # LLM eşiği
MAX_SEND_PER_RUN = 25   # tek turda bildirim tavanı
MAX_YAS_SAAT = 24       # geçmiş tarihli haber en fazla bu kadar eski olabilir
KOR_ORAN = 0.34         # kaynakların bu oranı hata verirse "sistem kör"


def load_config(scan: str):
    root = Path(__file__).resolve().parent.parent
    sources = yaml.safe_load((root / "config/sources.yml").read_text())["sources"]
    sozluk = yaml.safe_load((root / "config/keywords.yml").read_text())
    artists = yaml.safe_load((root / "config/artists.yml").read_text())["artists"]

    # Sanatçı adları "kesin" katmanına girer: biri geçiyorsa haber bizimdir.
    # "~" ile işaretliler (Duman, Ceza, Elif…) sadece sahne bağlamıyla eşleşir.
    kesin_ekle, temiz_artists = [], []
    for a in artists:
        a = (a or "").strip()
        if not a:
            continue
        kesin_ekle.append(a if a.startswith("~") else f"={a}")
        temiz_artists.append(a.lstrip("~").strip())

    # Sanatçılar sadece Almanca/yerel gruplara eklenir. Türkçe haber
    # metninde "ceza", "duman", "sıla" sürekli geçer — oraya eklenmez.
    for grup in ("yerel", "almanya_ulusal", "etkinlik_avrupa"):
        hedef = sozluk["gruplar"].setdefault(grup, {})
        hedef["kesin"] = list(hedef.get("kesin") or []) + kesin_ekle
    # Mekan sayfalarında sanatçı adı KAPI'nın parçası: adı geçmiyorsa
    # ve Türkçe sinyal de yoksa o konser bizi ilgilendirmiyor.
    mekan = sozluk["gruplar"].setdefault("mekan", {})
    mekan["zorunlu"] = list(mekan.get("zorunlu") or []) + kesin_ekle

    sources = [s for s in sources if s.get("scan", "news") == scan]
    return sources, sozluk, temiz_artists


def tarih_filtresi(items: list[dict]) -> list[dict]:
    """Çok eski haberleri eler. GELECEK tarihli içerik hep kalır."""
    simdi = datetime.now(timezone.utc)
    sinir = simdi - timedelta(hours=MAX_YAS_SAAT)
    kalan, elenen = [], 0
    for it in items:
        t = it.get("tarih")
        if t is None or t >= sinir:   # tarihsiz veya yeterince taze
            kalan.append(it)          # gelecek tarih de bu dala düşer
        else:
            elenen += 1
    if elenen:
        print(f"[TARİH] {elenen} eski item elendi (>{MAX_YAS_SAAT} saat)")
    return kalan


def _baslik_anahtari(title: str) -> str:
    kelimeler = "".join(ch if ch.isalnum() or ch == " " else " "
                        for ch in title.lower()).split()
    return " ".join(kelimeler[:6])


def kopyalari_birlestir(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Aynı haberin farklı gazetelerdeki kopyalarından birini tutar."""
    temsilci, kopya, gorulen = [], [], set()
    for it in items:
        anahtar = _baslik_anahtari(it["title"])
        if anahtar in gorulen:
            kopya.append(it)
        else:
            gorulen.add(anahtar)
            temsilci.append(it)
    if kopya:
        print(f"[KOPYA] {len(kopya)} neredeyse aynı başlık birleştirildi")
    return temsilci, kopya


def run(scan: str, dry_run: bool, test_only: bool, report: bool = False) -> int:
    sources, sozluk, artists = load_config(scan)

    # 1. Çek
    items, saglik = fetchers.fetch_all(sources)
    if scan == "events":
        items += concerts.fetch_all(artists)

    if test_only:
        print(f"\n[TEST] '{scan}' taraması: {len(items)} item")
        bozuk = [f"{n}: {d}" for n, d in saglik if d.startswith("HATA")]
        print(f"[TEST] Sorunlu kaynak: {len(bozuk)}")
        for satir in bozuk:
            print("  -", satir)
        if notifier.configured() and not dry_run:
            notifier.send_report(
                f"\U0001F527 Kaynak testi ({scan}) — {len(items)} item\n",
                bozuk or ["Tüm kaynaklar çalışıyor."])
        return 0

    # 2. Tarih filtresi — gelecek tarihli içerik korunur
    items = tarih_filtresi(items)

    # 3. Tarama içi dedup
    benzersiz, gorulen_link = [], set()
    for it in items:
        if it["link"] and it["link"] not in gorulen_link:
            benzersiz.append(it)
            gorulen_link.add(it["link"])
    items = benzersiz

    # 4. Hafıza
    seen = state.load()
    items = state.filter_new(items, seen)

    # 5. İlk tur sessizliği — yeni kaynakların birikmiş içeriği gönderilmez
    yeni_kaynaklar = state.ilk_turu_bekleyenler([s["name"] for s in sources])
    if yeni_kaynaklar:
        susturulan = [it for it in items if it["source"] in yeni_kaynaklar]
        items = [it for it in items if it["source"] not in yeni_kaynaklar]
        print(f"[İLK TUR] {len(yeni_kaynaklar)} yeni kaynak, "
              f"{len(susturulan)} içerik sessizce hafızaya alındı")
    else:
        susturulan = []

    # 6. Ağırlıklı puanlama
    adaylar, sinirda = scoring.uygula(items, sozluk)
    aday_linkler = {it["link"] for it in adaylar}
    aday_olmayan = [it for it in items if it["link"] not in aday_linkler]

    # 6a. "direct" kaynaklar (konsolosluk, NINA): zaten Türkçe ve resmi,
    #     LLM'e sokmak boşa kota.
    dogrudan = [{**it, "puan": 10, "ozet": "",
                 "kategori": "uyari" if it["title"].startswith("UYARI") else "resmi"}
                for it in adaylar if it.get("direct")]
    adaylar = [it for it in adaylar if not it.get("direct")]

    # 6b. Gazete kopyaları
    adaylar, kopyalar = kopyalari_birlestir(adaylar)

    # 7. LLM ikinci hakem (varsa)
    llm_notu = ""
    if classifier.available():
        sonuc = classifier.classify_all(adaylar, min_score=MIN_SCORE)
        kazananlar = sonuc["winners"]
        degerlendirilen = sonuc["evaluated"]
        ertelenen = sonuc["deferred"]
        if ertelenen:
            llm_notu = f"{len(ertelenen)} aday kota nedeniyle ertelendi"
        if sonuc["failed"]:
            llm_notu += (" · " if llm_notu else "") + \
                f"{len(sonuc['failed'])} aday puanlanamadı"
        print(f"[LLM] model={sonuc['model']} · {len(kazananlar)} kazanan")
    else:
        # Anahtar yok: puanlama zaten eledi, hepsi gider.
        print("[LLM] Anahtar yok — sadece kelime puanlaması kullanılıyor")
        kazananlar = [{**it, "llm_checked": False, "kategori": "haber",
                       "puan": it.get("skor")} for it in adaylar]
        degerlendirilen = adaylar
        ertelenen = []
        llm_notu = "LLM kapalı — kelime puanlaması"

    # 8. Sırala, tavanı uygula
    kazananlar = dogrudan + kazananlar
    kazananlar.sort(key=lambda it: it.get("puan") or 0, reverse=True)
    if len(kazananlar) > MAX_SEND_PER_RUN:
        print(f"[SEÇİM] {len(kazananlar)} kazanandan ilk {MAX_SEND_PER_RUN} "
              "tanesi gönderilecek")
        kazananlar = kazananlar[:MAX_SEND_PER_RUN]

    print(f"[SONUÇ] {len(kazananlar)} bildirim gönderilecek")

    # 9. Telegram
    gonderilen, basarisiz = [], []
    for it in kazananlar:
        if dry_run:
            print(f"[DRY-RUN] {it.get('puan')} | {it.get('eslesme', '')} | "
                  f"{it['title'][:60]}")
            continue
        if notifier.send(it):
            gonderilen.append(it)
        else:
            basarisiz.append(it)
        time.sleep(SEND_SLEEP_S)

    if dry_run:
        print("\n[DRY-RUN] Sınırda kalanlar (eşiği geçemeyenler):")
        for it in sinirda:
            print(f"   {it['skor']:>3} | {it.get('eslesme', '')} | "
                  f"{it['title'][:60]}")
        print("[DRY-RUN] Hafıza dosyaları değiştirilmedi")
        return 0

    # 10. Hafıza: gönderilemeyenler ve ertelenenler YAZILMAZ,
    #     bir sonraki turda tekrar denenirler.
    ertelenen_link = {it["link"] for it in ertelenen}
    basarisiz_link = {it["link"] for it in basarisiz}
    yazilacak = [it for it in (aday_olmayan + susturulan + kopyalar
                               + dogrudan + degerlendirilen)
                 if it["link"] not in ertelenen_link
                 and it["link"] not in basarisiz_link]
    state.mark_seen(seen, yazilacak)
    state.save(seen)
    if yeni_kaynaklar:
        state.ilk_turu_isaretle(yeni_kaynaklar)

    # 11. Kaynak sağlığı
    yeni_olenler, hatali = state.saglik_guncelle(saglik)
    if yeni_olenler:
        notifier.send_report(
            "\U0001F6A8 Bu kaynaklar uzun süredir veri vermiyor:\n", yeni_olenler)
    if sources and hatali / len(sources) > KOR_ORAN:
        notifier.send_report(
            "⚠️ SİSTEM KÖR OLABİLİR\n",
            [f"{hatali}/{len(sources)} kaynak hata verdi.",
             "Actions log'unda [FETCH] satırlarına bak."])

    # 12. Günlük rapor + sınırda kalanlar
    if report:
        notifier.send_report(
            "\U0001F4E1 Günlük radar raporu\n",
            [f"Taranan kaynak: {len(sources)} ({hatali} hatalı)",
             f"Yeni içerik: {len(items)}",
             f"Aday: {len(adaylar) + len(dogrudan)}",
             f"Gönderilen: {len(gonderilen)}",
             llm_notu or "LLM: sorunsuz"])
        if sinirda:
            notifier.send_report(
                "\U0001F50E Sınırda kalanlar — bunlardan geçmesi gereken var mı?\n",
                [f"{it['skor']} | {it['title'][:80]}" for it in sinirda])

    if basarisiz:
        print(f"[HATA] {len(basarisiz)} mesaj iletilemedi")
        return 1
    print("[BİTTİ]")
    return 0


def main():
    if "--ping" in sys.argv:
        ok = notifier.send_text(
            "✅ Radar bağlantı testi — bu mesajı görüyorsan Telegram sağlam.")
        print("[PING]", "OK" if ok else "BAŞARISIZ")
        sys.exit(0 if ok else 1)

    scan = "events" if ("--events" in sys.argv or "--concerts" in sys.argv) else "news"
    sys.exit(run(scan=scan,
                 dry_run="--dry-run" in sys.argv,
                 test_only="--test-sources" in sys.argv,
                 report="--report" in sys.argv))


if __name__ == "__main__":
    main()
