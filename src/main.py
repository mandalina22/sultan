"""Münih Radar — ana akış (LLM'siz, saf kelime eşleşmeli mimari).

Mantık: Eşleşen her içerik DİREKT Telegram'a gider. Puanlama, API
anahtarı, kota — hiçbiri yok. Seçiciliği kullanıcı yapar.

Kural özeti:
- Münih yerel basını  -> keyword listesi + sanatçı listesi eşleşmesi
- Tagesschau          -> türk/türkiye/münchen vb. (germany_national)
- Türk ulusal medyası -> dar "büyük olay" listesi (turkey_major)
- Konsolosluk/konser  -> filtresiz, her yeni içerik gider

Çalıştırma:
    python -m src.main                  # normal tarama (haber + duyuru)
    python -m src.main --concerts       # sanatçı listesinden konser taraması
    python -m src.main --test-sources   # sadece kaynakları test et
    python -m src.main --dry-run        # her şeyi yap ama gönderme
    python -m src.main --ping           # sadece Telegram bağlantısını test et
"""

import sys
import time
from pathlib import Path

import yaml

from src import concerts, fetchers, notifier, prefilter, state

SEND_SLEEP_S = 3  # Telegram'ın chat başına hız limitine takılmamak için


def load_config():
    sources = yaml.safe_load(Path("config/sources.yml").read_text())["sources"]
    keyword_groups = yaml.safe_load(Path("config/keywords.yml").read_text())
    # Sanatçı isimleri de eşleşme listesidir: yerel basında veya
    # Tagesschau'da bir sanatçının adı geçiyorsa bizi ilgilendirir.
    artists = yaml.safe_load(Path("config/artists.yml").read_text())["artists"]
    artist_lows = [a.lower() for a in artists]
    for group in ("default", "germany_national"):
        keyword_groups[group] = keyword_groups[group] + artist_lows
    return sources, keyword_groups


def main():
    test_only = "--test-sources" in sys.argv
    dry_run = "--dry-run" in sys.argv
    concerts_mode = "--concerts" in sys.argv

    if "--ping" in sys.argv:
        ok = notifier.send_text("✅ Radar bağlantı testi — bu mesajı görüyorsan Telegram tarafı sağlam.")
        print("[PING]", "OK" if ok else "BAŞARISIZ")
        sys.exit(0 if ok else 1)

    sources, keyword_groups = load_config()

    # 1. Tara
    if concerts_mode:
        artists = yaml.safe_load(Path("config/artists.yml").read_text())["artists"]
        items = concerts.fetch_all(artists)
    else:
        items = fetchers.fetch_all(sources)
    if test_only:
        print(f"\n[TEST] Toplam {len(items)} item çekildi. Kaynak testi bitti.")
        return

    # 1.5 Tarama içi dedup: aynı link iki feed'den gelirse teke indir
    unique, seen_links = [], set()
    for it in items:
        if it["link"] not in seen_links:
            unique.append(it)
            seen_links.add(it["link"])
    items = unique

    # 2. Daha önce görülenleri ele
    seen = state.load()
    items = state.filter_new(items, seen)

    # 3. Kelime eşleşmesi — eşleşen herkes kazanır, hakem yok
    winners = prefilter.apply(items, keyword_groups)
    print(f"[SONUÇ] {len(winners)} eşleşme bildirilecek")

    # 4. Telegram
    sent_ok = 0
    send_attempts = 0
    for it in winners:
        if dry_run:
            print(f"[DRY-RUN] Gönderilecekti: {it['title'][:70]}")
            continue
        send_attempts += 1
        if notifier.send(it):
            sent_ok += 1
        time.sleep(SEND_SLEEP_S)

    # 5. State güncelle (eşleşmeyenler dahil hepsi görüldü sayılır)
    state.mark_seen(seen, items)
    state.save(seen)

    # Günlük kalp atışı: konser taraması günde bir çalıştığı için oraya
    # bağlı — her gün EN AZ bir mesaj garantisi.
    if concerts_mode and not dry_run:
        heartbeat = ("📡 Günlük radar raporu: sistem çalışıyor.\n"
                     f"Konser taraması: {len(items)} yeni bulgu, "
                     f"{sent_ok} bildirim gönderildi.")
        if not notifier.send_text(heartbeat):
            print("[HATA] Kalp atışı gönderilemedi — TELEGRAM_BOT_TOKEN / "
                  "TELEGRAM_CHAT_ID kontrol edilmeli")
            sys.exit(1)

    # Sessiz ölüm koruması: gönderim tümden çöktüyse run kırmızı yansın
    if send_attempts > 0 and sent_ok == 0:
        print("[HATA] Hiçbir Telegram mesajı iletilemedi — token/chat_id "
              "kontrol edilmeli")
        sys.exit(1)
    print("[BİTTİ]")


if __name__ == "__main__":
    main()
