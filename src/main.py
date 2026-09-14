"""Münih Radar — ana akış.

HAT: kaynaklar -> tarih filtresi -> dedup -> hafıza
     -> AĞIRLIKLI KELİME PUANLAMASI -> Telegram

LLM YOK. Aday belirleme tamamen kelime puanlamasıyla yapılır
(src/scoring.py). Hiçbir dış API anahtarı gerekmez; sadece Telegram
ve (etkinlik taraması için, isteğe bağlı) Ticketmaster.

TASARIM KARARLARI

1) Tarih: GEÇMİŞ tarihli bir haber en fazla MAX_YAS_SAAT kadar eski
   olabilir. GELECEK tarihli içerik (2036'ya duyurulmuş etkinlik bile)
   hiçbir zaman elenmez.

2) İlk tur sessizliği SADECE tarihsiz kaynaklara uygulanır
   (konsolosluk sayfası gibi). RSS kaynaklarında tarih zaten var,
   orada susturma yapılmaz — yoksa ilk turda hiçbir haber gelmez.

3) Üst üste 3 tur hata veren kaynak otomatik devre dışı kalır ve
   bir kez bildirilir. Her turda hata mesajı yağmaz.
"""

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from src import concerts, fetchers, notifier, scoring, state

SEND_SLEEP_S = 3        # Telegram hız limiti
MAX_SEND_PER_RUN = 25   # tek turda bildirim tavanı
MAX_YAS_SAAT = 24       # geçmiş tarihli haber en fazla bu kadar eski olabilir


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

    # Türkçe haber metninde "ceza", "duman", "sıla" sürekli geçtiği için
    # sanatçılar sadece Almanca/yerel gruplara eklenir.
    for grup in ("yerel", "almanya_ulusal", "etkinlik_avrupa"):
        hedef = sozluk["gruplar"].setdefault(grup, {})
        hedef["kesin"] = list(hedef.get("kesin") or []) + kesin_ekle
    mekan = sozluk["gruplar"].setdefault("mekan", {})
    mekan["zorunlu"] = list(mekan.get("zorunlu") or []) + kesin_ekle

    sources = [s for s in sources if s.get("scan", "news") == scan]
    return sources, sozluk, temiz_artists


def tarih_filtresi(items: list[dict]) -> list[dict]:
    """Çok eski haberleri eler. GELECEK tarihli içerik hep kalır."""
    sinir = datetime.now(timezone.utc) - timedelta(hours=MAX_YAS_SAAT)
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
    tum_sources, sozluk, artists = load_config(scan)

    # Üst üste hata veren kaynakları atla (test modunda hepsi denenir)
    kapali = set() if test_only else state.devre_disi_kaynaklar()
    sources = [s for s in tum_sources if s["name"] not in kapali]
    if kapali:
        print(f"[KAYNAK] {len(kapali)} kaynak üst üste hata verdiği için "
              f"atlandı: {', '.join(sorted(kapali))}")

    # 1. Çek
    items, saglik = fetchers.fetch_all(sources)
    if scan == "events":
        items += concerts.fetch_all(artists)

    if test_only:
        print(f"\n[TEST] '{scan}' taraması: {len(items)} item")
        bozuk = [f"{n}: {d}" for n, d in saglik if d.startswith("HATA")]
        print(f"[TEST] Sorunlu kaynak: {len(bozuk)}/{len(sources)}")
        for satir in bozuk:
            print("  -", satir)
        if notifier.configured() and not dry_run:
            notifier.send_report(
                f"\U0001F527 Kaynak testi ({scan}) — {len(items)} item, "
                f"{len(bozuk)}/{len(sources)} sorunlu\n",
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

    # 5. İlk tur sessizliği — SADECE tarihsiz kaynaklar için.
    #    Konsolosluk sayfasında tarih yok, orada birikmiş 3 yıllık duyuru
    #    "yeni" görünür. RSS'te tarih var, orada susturmaya gerek yok.
    tarihsiz_kaynaklar = {it["source"] for it in items if it.get("tarih") is None}
    bekleyen = state.ilk_turu_bekleyenler(sorted(tarihsiz_kaynaklar))
    susturulan = []
    if bekleyen:
        susturulan = [it for it in items
                      if it["source"] in bekleyen and it.get("tarih") is None]
        kalan_linkler = {it["link"] for it in susturulan}
        items = [it for it in items if it["link"] not in kalan_linkler]
        print(f"[İLK TUR] tarihsiz kaynak ({', '.join(sorted(bekleyen))}): "
              f"{len(susturulan)} birikmiş içerik sessizce hafızaya alındı")

    # 6. Ağırlıklı puanlama — aday belirleme burada biter
    adaylar, sinirda = scoring.uygula(items, sozluk)
    aday_linkler = {it["link"] for it in adaylar}
    aday_olmayan = [it for it in items if it["link"] not in aday_linkler]

    # 6a. Gazete kopyaları
    adaylar, kopyalar = kopyalari_birlestir(adaylar)

    # 6b. "direct" kaynaklar ayrı etiketlenir (konsolosluk, NINA)
    kazananlar = []
    for it in adaylar:
        if it.get("direct"):
            kategori = "uyari" if it["title"].startswith("UYARI") else "resmi"
        else:
            kategori = "haber"
        kazananlar.append({**it, "kategori": kategori, "puan": None})

    # 7. Sırala, tavanı uygula
    kazananlar.sort(key=lambda it: it.get("skor") or 0, reverse=True)
    if len(kazananlar) > MAX_SEND_PER_RUN:
        print(f"[SEÇİM] {len(kazananlar)} adaydan en yüksek puanlı "
              f"{MAX_SEND_PER_RUN} tanesi gönderilecek")
        kazananlar = kazananlar[:MAX_SEND_PER_RUN]

    print(f"[SONUÇ] {len(kazananlar)} bildirim gönderilecek")

    # 8. Telegram
    gonderilen, basarisiz = [], []
    for it in kazananlar:
        if dry_run:
            print(f"[DRY-RUN] {it.get('skor')} | {it.get('eslesme', '')} | "
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

    # 9. Hafıza: gönderilemeyenler YAZILMAZ, sonraki turda tekrar denenir
    basarisiz_link = {it["link"] for it in basarisiz}
    yazilacak = [it for it in (aday_olmayan + susturulan + kopyalar + adaylar)
                 if it["link"] not in basarisiz_link]
    state.mark_seen(seen, yazilacak)
    state.save(seen)
    if bekleyen:
        state.ilk_turu_isaretle(bekleyen)

    # 10. Kaynak sağlığı — yeni devre dışı kalanları bir kez bildir
    yeni_kapananlar = state.saglik_guncelle(saglik)
    if yeni_kapananlar:
        notifier.send_report(
            "\U0001F527 Şu kaynaklar üst üste hata verdi, geçici olarak "
            "devre dışı bırakıldı:\n",
            yeni_kapananlar + ["", "Düzelince tekrar açılır."])

    # 11. Günlük rapor
    if report:
        notifier.send_report(
            "\U0001F4E1 Günlük radar raporu\n",
            [f"Taranan kaynak: {len(sources)}",
             f"Yeni içerik: {len(items)}",
             f"Aday: {len(kazananlar)}",
             f"Gönderilen: {len(gonderilen)}"])
        if sinirda:
            notifier.send_report(
                "\U0001F50E Sınırda kalanlar — geçmesi gereken var mı?\n",
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
