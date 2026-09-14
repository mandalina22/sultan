"""Konser/gösteri tarayıcı — v2.

ANA HAT — Ticketmaster Discovery API (resmi, ücretsiz anahtar):
  Münih (120 km) ve Nürnberg (100 km) çevresindeki TÜM etkinlikleri
  çeker (müzik + komedi + tiyatro dahil), sonra iki süzgeçten geçirir:
  (a) sanatçı listemizden biri mi? -> direkt bildir
  (b) mega mekanda mı (Olympiahalle vb.)? -> bildir (haber taramasındaki
      mega-olay mantığının bilet ayağı)
  Türk diaspora konserleri Almanya'da büyük oranda Ticketmaster/Eventim
  ekosisteminde satılır; Manifest örneği Ticketmaster'da doğrulandı.
  Anahtar: developer.ticketmaster.com -> ücretsiz hesap -> Consumer Key
  -> GitHub Secrets'a TICKETMASTER_API_KEY olarak ekle.

YAN HAT — Bandsintown (anahtarsız):
  Sanatçı bazlı sorgu, 8 paralel işçiyle (~15 sn). Batılı platformlarda
  listelenen turneleri yakalar; Türk organizatör konserlerinde kapsamı
  zayıftır, o boşluğu Ticketmaster hattı kapatır.

Üçüncü ağ (bu dosyada değil): 20 dakikalık haber taraması — sanatçı
isimleri keyword listesine ekli olduğu için bilet satışı basına
yansıdığı anda oradan da yakalanır.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

HEADERS = {"User-Agent": "BizimMunih/1.0 (kisisel proje)"}

# Kapsama bölgeleri: (etiket, "enlem,boylam", yarıçap km)
REGIONS = [
    ("München+120km", "48.1374,11.5755", "120"),
    ("Nürnberg+100km", "49.4521,11.0767", "100"),
]
MEGA_VENUES = (
    "olympiastadion", "olympiahalle", "sap garden", "königsplatz",
    "olympiapark", "zenith", "circus krone", "tonhalle",
    "löwensaal", "meistersingerhalle", "arena nürnberg", "frankenstadion",
)
CITY_WORDS = ("münchen", "munich", "muenchen", "nürnberg", "nuremberg", "nuernberg")
REGION_WORDS = ("bavaria", "bayern")


# ---------------------------------------------------------------- Ticketmaster
def fetch_ticketmaster(artists: list[str]) -> list[dict]:
    key = os.environ.get("TICKETMASTER_API_KEY")
    if not key:
        print("[KONSER] Ticketmaster ATLANDI — TICKETMASTER_API_KEY secret'ı "
              "tanımlı değil. Türk diaspora konserlerinin ana kapsaması bu "
              "hatta; anahtarı eklemek şiddetle önerilir.")
        return []

    artist_lows = [a.lower() for a in artists]
    items, seen_ids = [], set()

    for label, latlong, radius in REGIONS:
        page = 0
        while page < 3:  # bölge başına en fazla 3 sayfa (~600 etkinlik)
            try:
                resp = requests.get(
                    "https://app.ticketmaster.com/discovery/v2/events.json",
                    params={
                        "apikey": key, "latlong": latlong, "radius": radius,
                        "unit": "km", "countryCode": "DE", "size": 199,
                        "page": page, "sort": "date,asc",
                    },
                    headers=HEADERS, timeout=30,
                )
                if resp.status_code != 200:
                    print(f"[KONSER HATA] Ticketmaster {label} s.{page}: "
                          f"HTTP {resp.status_code}")
                    break
                data = resp.json()
            except Exception as e:
                print(f"[KONSER HATA] Ticketmaster {label}: {e}")
                break

            events = data.get("_embedded", {}).get("events", [])
            for ev in events:
                ev_id = ev.get("id", "")
                if ev_id in seen_ids:
                    continue
                name = ev.get("name", "")
                emb = ev.get("_embedded", {})
                venue = (emb.get("venues") or [{}])[0]
                venue_name = venue.get("name", "")
                city = venue.get("city", {}).get("name", "")
                attractions = " ".join(
                    a.get("name", "") for a in emb.get("attractions", []))
                haystack = f"{name} {attractions}".lower()

                artist_hit = any(a in haystack for a in artist_lows)
                mega_hit = any(v in venue_name.lower() for v in MEGA_VENUES)
                if not (artist_hit or mega_hit):
                    continue

                seen_ids.add(ev_id)
                date = ev.get("dates", {}).get("start", {}).get("localDate", "")
                items.append({
                    "source": "Ticketmaster",
                    "title": f"KONSER: {name} — {venue_name}, {city} ({date})",
                    "summary": f"{name} · {venue_name} · {city} · {date}",
                    "link": ev.get("url") or f"tm://{ev_id}",
                    "trusted": artist_hit,   # sanatçımızsa filtresiz geçer
                    "keyword_list": "default",
                })

            total = data.get("page", {}).get("totalPages", 1)
            page += 1
            if page >= total:
                break
            time.sleep(0.3)
    return items


# ----------------------------------------------------------------- Bandsintown
def _is_local(city: str, region: str, country: str) -> bool:
    c, r, co = (city or "").lower(), (region or "").lower(), (country or "").lower()
    if co not in ("germany", "deutschland", "de"):
        return False
    return any(w in c for w in CITY_WORDS) or any(w in r for w in REGION_WORDS)


def _bt_one(artist: str) -> tuple[str, list[dict], bool]:
    """Tek sanatçı sorgusu. Döner: (sanatçı, konserler, sayfası_var_mı)"""
    try:
        resp = requests.get(
            f"https://rest.bandsintown.com/artists/{quote(artist)}/events",
            params={"app_id": "bizim_munih", "date": "upcoming"},
            headers=HEADERS, timeout=15,
        )
        if resp.status_code != 200:
            return artist, [], False
        data = resp.json()
        if not isinstance(data, list):
            return artist, [], False
        found = []
        for ev in data:
            venue = ev.get("venue", {})
            if not _is_local(venue.get("city"), venue.get("region"),
                             venue.get("country")):
                continue
            date = (ev.get("datetime") or "")[:10]
            found.append({
                "source": "Bandsintown",
                "title": f"KONSER: {artist} — {venue.get('name', '?')}, "
                         f"{venue.get('city', '')} ({date})",
                "summary": f"{artist} konseri · {venue.get('city', '')} · {date}",
                "link": ev.get("url") or f"bt://{artist}/{date}",
                "trusted": True,
                "keyword_list": "default",
            })
        return artist, found, True
    except Exception as e:
        print(f"[KONSER HATA] Bandsintown {artist}: {e}")
        return artist, [], False


def fetch_bandsintown(artists: list[str]) -> list[dict]:
    items, with_page = [], 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for artist, found, has_page in ex.map(_bt_one, artists):
            items.extend(found)
            with_page += int(has_page)
    print(f"[KONSER] Bandsintown teşhis: {len(artists)} sanatçıdan "
          f"{with_page} tanesinin platformda sayfası var")
    return items


# ---------------------------------------------------------------------- toplam
def fetch_all(artists: list[str]) -> list[dict]:
    tm = fetch_ticketmaster(artists)
    print(f"[KONSER] Ticketmaster: {len(tm)} eşleşme")
    bt = fetch_bandsintown(artists)
    print(f"[KONSER] Bandsintown: {len(bt)} eşleşme")
    # iki kaynak aynı konseri bulursa link farklı olur; kaba başlık dedup'u
    seen_titles, merged = set(), []
    for it in tm + bt:
        key = it["title"].lower()[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            merged.append(it)
    return merged
