"""Münih Radar — ana akış.

BOŞ HAT: kaynaklar -> tarama içi dedup -> daha önce görülenleri ele
         -> kelime ön filtresi -> LLM doğrulaması -> Telegram

TASARIM KARARI — neden LLM geri geldi:
Eski sürümde src/classifier.py dosyası duruyordu ama main.py onu HİÇ
import etmiyordu; yani "API ile kontrol" hiç çalışmadı. Kelime eşleşen
her şey doğrudan Telegram'a gidiyordu, bu da günde yüzlerce alakasız
bildirim demekti. Şimdi ön filtre GENİŞ (bir şey kaçmasın), LLM ise
HAKEM (gürültü geçmesin).

ÇÖKMEZLİK: Gemini anahtarı yoksa veya kota bittiyse bot durmaz —
"kelime modu"na düşer, mesajlara ⚠️ koyar ve sadece yüksek güvenli
kaynakları gönderir. Kota yüzünden puanlanamayanlar seen'e YAZILMAZ,
ertesi tarama kaldığı yerden devam eder.

Çalıştırma:
    python -m src.main                  # haber taraması (saatte bir)
    python -m src.main --events         # etkinlik + konser taraması (günde 2)
    python -m src.main --events --report  # + günlük rapor mesajı
    python -m src.main --test-sources   # sadece kaynakları test et
    python -m src.main --dry-run        # her şeyi yap ama gönderme
    python -m src.main --ping           # Telegram bağlantı testi
"""

import sys
import time
from pathlib import Path

import yaml

from src import classifier, concerts, fetchers, notifier, prefilter, state

SEND_SLEEP_S = 3       # Telegram hız limiti
MIN_SCORE = 6          # LLM eşiği
MAX_SEND_PER_RUN = 25  # tek turda bildirim tavanı (bildirim seli koruması)

# LLM yokken kelime moduyla göndermeye devam edilecek kaynak grupları.
# Bunlar dar filtreli veya resmi kaynaklar; gürültü riski düşük.
SAFE_WITHOUT_LLM = {"turkey_major", "bavaria_reach", "mvg_relevant",
                    "event_turkish", "community"}


def load_config(scan: str):
    root = Path(__file__).resolve().parent.parent
    sources = yaml.safe_load((root / "config/sources.yml").read_text())["sources"]
    keyword_groups = yaml.safe_load((root / "config/keywords.yml").read_text())
    artists = yaml.safe_load((root / "config/artists.yml").read_text())["artists"]

    # Sanatçı adları da eşleşme kelimesidir. Tam eşleşme zorunlu.
    # artists.yml'de "~" ile işaretlenenler (Duman, Ceza, Elif, Sıla...)
    # Türkçede günlük kelime; onlar sadece sahne bağlamıyla eşleşir.
    # Diğerleri "=" ile tam kelime olarak eşleşir.
    artist_keys, clean_artists = [], []
    for a in artists:
        a = (a or "").strip()
        if not a:
            continue
        if a.startswith("~"):
            artist_keys.append(a)
            clean_artists.append(a[1:].strip())
        else:
            artist_keys.append(f"={a}")
            clean_artists.append(a)
    # diaspora / turkey_major gruplarına EKLENMEZ: Türkçe haber metninde
    # "ceza", "duman", "sıla" sürekli geçer, her biri boşa API çağrısı olur.
    for group in ("default", "germany_national", "event_turkish"):
        keyword_groups[group] = keyword_groups.get(group, []) + artist_keys

    # scan alanı: "news" (varsayılan) veya "events"
    sources = [s for s in sources if s.get("scan", "news") == scan]
    return sources, keyword_groups, clean_artists


def _title_key(title: str) -> str:
    """Aynı haberin farklı gazetelerdeki kopyalarını yakalamak için kaba
    anahtar: küçük harf, noktalama yok, ilk 6 kelime."""
    words = "".join(ch if ch.isalnum() or ch == " " else " "
                    for ch in title.lower()).split()
    return " ".join(words[:6])


def collapse_duplicates(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Başlığı neredeyse aynı olanlardan sadece ilkini LLM'e gönderir.
    Döner: (temsilciler, elenen kopyalar). Kopyalar seen'e yazılır ama
    puanlanmaz — her kopya boşa giden token'dı."""
    reps, dups, seen_keys = [], [], set()
    for it in items:
        key = _title_key(it["title"])
        if key in seen_keys:
            dups.append(it)
        else:
            seen_keys.add(key)
            reps.append(it)
    if dups:
        print(f"[KOPYA] {len(dups)} neredeyse aynı başlık LLM'e gönderilmedi")
    return reps, dups


def run(scan: str, dry_run: bool, test_only: bool, report: bool = False) -> int:
    sources, keyword_groups, artists = load_config(scan)

    # 1. Kaynakları çek
    items, health = fetchers.fetch_all(sources)
    if scan == "events":
        items += concerts.fetch_all(artists)

    if test_only:
        print(f"\n[TEST] '{scan}' taraması: {len(items)} item")
        bad = [f"{n}: {s}" for n, s in health if s.startswith("HATA")]
        print(f"[TEST] Sorunlu kaynak sayısı: {len(bad)}")
        for line in bad:
            print("  -", line)
        if notifier.configured() and not dry_run:
            notifier.send_report(
                f"\U0001F527 Kaynak testi ({scan}) — {len(items)} item\n",
                bad or ["Tüm kaynaklar çalışıyor."])
        return 0

    # 2. Tarama içi dedup (aynı link iki feed'de olabilir)
    unique, seen_links = [], set()
    for it in items:
        if it["link"] and it["link"] not in seen_links:
            unique.append(it)
            seen_links.add(it["link"])
    items = unique

    # 3. Daha önce görülenleri ele
    seen = state.load()
    items = state.filter_new(items, seen)

    # 4. Kelime ön filtresi (GENİŞ)
    candidates = prefilter.apply(items, keyword_groups)
    # Not: candidates içindeki sözlüklere "match" alanı eklendiği için
    # doğrudan karşılaştırma çalışmaz — link üzerinden ayırıyoruz.
    candidate_links = {it["link"] for it in candidates}
    non_candidates = [it for it in items if it["link"] not in candidate_links]

    # 4a. "direct" kaynaklar (konsolosluk, NINA): zaten Türkçe ve resmi.
    #     LLM'e sokmak boşa kota — doğrudan gönderilir.
    direct_items = [
        {**it, "puan": 10, "ozet": "",
         "kategori": "uyari" if it["title"].startswith("UYARI") else "resmi"}
        for it in candidates if it.get("direct")]
    candidates = [it for it in candidates if not it.get("direct")]

    # 4b. Aynı haberin gazete kopyaları — bir tanesi yeter
    candidates, duplicates = collapse_duplicates(candidates)

    # 5. LLM hakemliği
    llm_note = ""
    if classifier.available():
        result = classifier.classify_all(candidates, min_score=MIN_SCORE)
        winners = result["winners"]
        evaluated = result["evaluated"]
        deferred = result["deferred"]
        if deferred:
            llm_note = f"{len(deferred)} aday kota nedeniyle ertelendi"
        if result["failed"]:
            llm_note += (" · " if llm_note else "") + \
                f"{len(result['failed'])} aday puanlanamadı"
        print(f"[LLM] model={result['model']} · {len(winners)} kazanan")
    else:
        # Anahtar yok -> kelime modu. Sadece düşük gürültülü gruplar gider.
        print("[LLM] GEMINI_API_KEY yok — kelime moduna düşülüyor")
        winners = [
            {**it, "llm_checked": False, "kategori": "haber", "puan": None}
            for it in candidates
            if it.get("trusted") or it.get("keyword_list") in SAFE_WITHOUT_LLM
        ]
        evaluated = candidates
        deferred = []
        llm_note = "LLM kapalı (GEMINI_API_KEY yok) — kelime modu"

    # 6. Önce en önemliler (direct kaynaklar en başta)
    winners = direct_items + winners
    winners.sort(key=lambda it: it.get("puan") or 0, reverse=True)
    if len(winners) > MAX_SEND_PER_RUN:
        print(f"[SEÇİM] {len(winners)} kazanandan ilk {MAX_SEND_PER_RUN} tanesi "
              "gönderilecek")
        winners = winners[:MAX_SEND_PER_RUN]

    print(f"[SONUÇ] {len(winners)} bildirim gönderilecek")

    # 7. Telegram — SADECE gerçekten gideni görülmüş say
    sent_items, failed_items = [], []
    for it in winners:
        if dry_run:
            print(f"[DRY-RUN] {it.get('puan')} | {it['title'][:70]}")
            continue
        if notifier.send(it):
            sent_items.append(it)
        else:
            failed_items.append(it)
        time.sleep(SEND_SLEEP_S)

    if dry_run:
        print("[DRY-RUN] state dosyaları değiştirilmedi")
        return 0

    # 8. State: gönderilemeyenler ve ertelenen adaylar seen'e YAZILMAZ,
    #    böylece bir sonraki turda tekrar denenirler.
    deferred_links = {it["link"] for it in deferred}
    failed_links = {it["link"] for it in failed_items}
    to_mark = [it for it in non_candidates + duplicates + direct_items + evaluated
               if it["link"] not in deferred_links
               and it["link"] not in failed_links]
    state.mark_seen(seen, to_mark)
    state.save(seen)

    # 9. Kaynak sağlığı — sessizce ölen kaynağı bildir
    newly_dead = state.update_health(health)
    if newly_dead:
        notifier.send_report(
            "\U0001F6A8 Bu kaynaklar uzun süredir veri vermiyor:\n", newly_dead)

    # 10. Günlük kalp atışı — sadece --report ile (workflow sabah turunda
    #     verir; etkinlik taraması günde iki kez çalıştığı için her turda
    #     rapor atmak gürültü olurdu). "Sistem yaşıyor" garantisi.
    if report:
        notifier.send_report(
            "\U0001F4E1 Günlük radar raporu\n",
            [f"Taranan kaynak: {len(sources)}",
             f"Yeni içerik: {len(items)}",
             f"Aday: {len(candidates)}",
             f"Gönderilen: {len(sent_items)}",
             llm_note or "LLM: sorunsuz"])

    if failed_items:
        print(f"[HATA] {len(failed_items)} mesaj iletilemedi")
        return 1
    print("[BİTTİ]")
    return 0


def main():
    if "--ping" in sys.argv:
        ok = notifier.send_text(
            "✅ Radar bağlantı testi — bu mesajı görüyorsan Telegram tarafı sağlam.")
        print("[PING]", "OK" if ok else "BAŞARISIZ")
        sys.exit(0 if ok else 1)

    scan = "events" if ("--events" in sys.argv or "--concerts" in sys.argv) else "news"
    sys.exit(run(scan=scan,
                 dry_run="--dry-run" in sys.argv,
                 test_only="--test-sources" in sys.argv,
                 report="--report" in sys.argv))


if __name__ == "__main__":
    main()
