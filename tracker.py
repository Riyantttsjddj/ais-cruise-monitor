#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Lacak Kapal — pelacak posisi kapal di peta, gaya Google Maps.

Sumber data : halaman publik MyShipTracking (utama) + CruiseMapper (cadangan).
              Keduanya mengizinkan path ini di robots.txt.
Peta        : Leaflet + OpenStreetMap / Esri Satellite / CartoDB Dark
Dependensi  : tidak ada — Python standard library saja.

Jalankan:
    python3 tracker.py                 # lalu buka http://127.0.0.1:8777
    python3 tracker.py --interval 90   # lebih hemat: ~110 MB/hari vs ~1,6 GB

Catatan penting: ini membaca halaman web publik, bukan API resmi, dan BUKAN
GPS real-time. Situs bisa mengubah struktur HTML-nya kapan saja, dan posisi
yang ditampilkan bisa tertinggal berjam-jam — terutama saat kapal sandar,
ketika kapal jarang memancarkan laporan baru. MyShipTracking juga tidak
mendukung cache (tanpa ETag/Last-Modified), jadi setiap pengecekan selalu
mengunduh halaman penuh. Panel di UI menampilkan umur data; itulah penanda
kesegaran yang bisa dipercaya.
"""

from __future__ import annotations

import argparse
import calendar
import html as html_mod
import json
import math
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
KNOTS_TO_KMH = 1.852
NM_TO_KM = 1.852

# Situs menolak user-agent yang tidak dikenal; ini UA browser yang sudah diuji.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
CRAWL_DELAY = 5.0          # detik antar-request ke host yang sama (robots.txt)
SAVE_MIN_INTERVAL = 30.0   # jeda minimum antar-penulisan state.json


# --------------------------------------------------------------- util teks


def text_of(fragment: str) -> str:
    """Buang tag HTML dan rapikan spasinya."""
    return " ".join(html_mod.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def strip_scripts(h: str) -> str:
    return re.sub(r"<script\b.*?</script>", " ", h, flags=re.S | re.I)


def parse_dt(value: str):
    """'2026-09-13 10:43' -> epoch. Sumber tidak menyebut zona waktu; kita
    anggap UTC. Nilai absolutnya bisa meleset beberapa jam, tapi urutannya benar
    sehingga aman untuk menyusun jejak."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", value or "")
    if not m:
        return None
    y, mo, d, h, mi = (int(x) for x in m.groups())
    return calendar.timegm((y, mo, d, h, mi, 0, 0, 0, 0))


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def haversine_nm(lat1, lon1, lat2, lon2) -> float:
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r_nm * math.asin(min(1.0, math.sqrt(a)))


# ------------------------------------------------------------- pengambil web


class Fetcher:
    """HTTP GET dengan throttle per-host, cache ETag, dan penghitung error."""

    def __init__(self):
        self._last_hit: dict[str, float] = {}
        self._cache: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _throttle(self, host: str):
        with self._lock:
            wait = CRAWL_DELAY - (time.time() - self._last_hit.get(host, 0.0))
            if wait > 0:
                time.sleep(wait)
            self._last_hit[host] = time.time()

    def get(self, url: str, timeout: int = 25):
        """Kembalikan (body, dari_cache). Body None kalau gagal."""
        host = urllib.parse.urlsplit(url).netloc
        entry = self._cache.get(url) or {}
        self._throttle(host)

        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if entry.get("etag"):
            req.add_header("If-None-Match", entry["etag"])
        if entry.get("last_modified"):
            req.add_header("If-Modified-Since", entry["last_modified"])

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", "replace")
                self._cache[url] = {
                    "etag": resp.headers.get("ETag"),
                    "last_modified": resp.headers.get("Last-Modified"),
                    "body": body,
                }
                return body, False
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and entry.get("body"):
                return entry["body"], True
            raise

    def invalidate(self, url: str):
        self._cache.pop(url, None)


# ------------------------------------------------------------------ sumber


@dataclass
class Snapshot:
    """Data mentah hasil satu sumber, sebelum digabung ke Ship."""
    source: str
    name: str = ""
    lat: float | None = None
    lon: float | None = None
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None
    nav_status: str = ""
    area: str = ""
    station: str = ""
    destination: str = ""
    draught: float | None = None
    callsign: str = ""
    flag: str = ""
    length: float | None = None
    beam: float | None = None
    ship_type: str = ""
    reported: str = ""          # teks waktu lapor apa adanya dari sumber
    reported_ts: float | None = None
    reported_human: str = ""    # "1 d ago" — relatif versi situsnya
    trip_distance: str = ""
    avg_speed: str = ""
    max_speed: str = ""
    weather: dict = field(default_factory=dict)
    history: list = field(default_factory=list)   # [[lat, lon, ts], ...]


class SourceError(RuntimeError):
    pass


def parse_tables(clean: str):
    """Ambil semua <table> jadi daftar dict {label: nilai}."""
    tables = []
    for tbl in re.findall(r"<table\b.*?</table>", clean, re.S | re.I):
        pairs: dict[str, str] = {}
        for th, td in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>",
                                 tbl, re.S | re.I):
            key, val = text_of(th), text_of(td)
            if key and val and key not in pairs:
                pairs[key] = val
        if pairs:
            tables.append(pairs)
    return tables


def vessel_url(ship) -> str:
    """MyShipTracking hanya menyajikan data posisi di URL slug lengkap
    (/vessels/<nama>-imo-<imo>-mmsi-<mmsi>). URL ?mmsi=… hanyalah halaman
    landasan tanpa data. Slug disusun dari nama; `url` di ships.json bisa
    dipakai untuk menimpanya kalau nama di situs berubah."""
    if getattr(ship, "url", ""):
        return ship.url
    name = ship.name or ship.label
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not slug or not ship.imo:
        raise SourceError("butuh nama + IMO, atau isi 'url' di ships.json")
    return (f"https://www.myshiptracking.com/vessels/"
            f"{slug}-imo-{ship.imo}-mmsi-{ship.mmsi}")


def fetch_myshiptracking(fetcher: Fetcher, ship) -> Snapshot:
    raw, _ = fetcher.get(vessel_url(ship))
    if not raw:
        raise SourceError("halaman kosong")
    if ship.mmsi not in raw:
        raise SourceError(f"halaman tidak memuat MMSI {ship.mmsi} — slug mungkin salah")
    clean = strip_scripts(raw)
    snap = Snapshot(source="MyShipTracking")

    # 0. Judul halaman memuat nama + tipe. Dua format yang pernah terlihat:
    #    "NAMA - Passenger (IMO: …, MMSI: …) | MyShipTracking"
    #    "NAMA Current Position (Passenger, MMSI: …) - MyShipTracking"
    title = re.search(r"<title>(.*?)</title>", raw, re.S | re.I)
    if title:
        head = text_of(title.group(1))
        head = re.sub(r"\s*[\-|]\s*MyShipTracking\s*$", "", head, flags=re.I).strip()
        head = re.sub(r"\s+Current Position\s*", " ", head, flags=re.I).strip()
        if " - " in head:
            name, _, kind = head.partition(" - ")
            snap.name = name.strip()
            snap.ship_type = re.sub(r"\s*\(.*\)\s*$", "", kind).strip()
        else:
            paren = re.search(r"\(([^,()]+)[,)]", head)
            if paren:
                snap.ship_type = paren.group(1).strip()
            snap.name = re.sub(r"\s*\(.*\)\s*$", "", head).strip()

    # 1. Blok "posisi saat ini" disimpan di dalam <script>.
    m = re.search(
        r'canvas_map_generate\(\s*"map_locator"\s*,\s*\d+\s*,\s*'
        r"(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)",
        raw)
    cur_ts = None
    if m:
        snap.lat, snap.lon = float(m.group(1)), float(m.group(2))
        snap.cog, snap.sog = float(m.group(3)), float(m.group(4))

    # 2. Kalimat prosa: area + koordinat + waktu lapor.
    prose = re.search(
        r"current position of (.+?) is in (.+?) with coordinates "
        r"(-?[\d.]+)°\s*/\s*(-?[\d.]+)°\s*as reported on ([\d\-: ]+?)\s*by AIS",
        text_of(clean), re.I)
    if prose:
        snap.area = prose.group(2).strip()
        snap.reported = prose.group(5).strip()
        cur_ts = parse_dt(snap.reported)
        if snap.lat is None:
            snap.lat, snap.lon = float(prose.group(3)), float(prose.group(4))

    # 3. Tabel spesifikasi, tabel perjalanan, dan tabel posisi — masing-masing
    #    berdiri sendiri, jadi tiap tabel diperiksa menurut kunci yang ada.
    tables = parse_tables(clean)
    for pairs in tables:
        if "MMSI" in pairs:
            snap.callsign = pairs.get("Call Sign", "") or snap.callsign
            snap.flag = pairs.get("Flag", "") or snap.flag
            snap.ship_type = pairs.get("Type", "") or snap.ship_type
            size = re.match(r"([\d.]+)\s*x\s*([\d.]+)", pairs.get("Size", ""))
            if size:
                snap.length, snap.beam = float(size.group(1)), float(size.group(2))
        if "Trip Distance" in pairs or "Draught" in pairs:
            snap.trip_distance = pairs.get("Trip Distance", "") or snap.trip_distance
            snap.avg_speed = pairs.get("AVG Speed", "") or snap.avg_speed
            snap.max_speed = pairs.get("MAX Speed", "") or snap.max_speed
            words = (pairs.get("Draught") or "").split()
            if words and to_float(words[0]) is not None:
                snap.draught = to_float(words[0])
        if "Course" in pairs:
            snap.nav_status = pairs.get("Status", "") or snap.nav_status
            snap.area = pairs.get("Area", "") or snap.area
            snap.station = pairs.get("Station", "") or snap.station
            snap.reported_human = pairs.get("Position Received", "") or snap.reported_human
            course = to_float((pairs.get("Course") or "").replace("°", ""))
            if course is not None:
                snap.cog = course
            words = (pairs.get("Speed") or "").split()
            if words and to_float(words[0]) is not None:
                snap.sog = to_float(words[0])
        for label, key in (("Temperature", "temperature"), ("Wind Speed", "wind"),
                           ("Pressure", "pressure"), ("Humidity", "humidity")):
            if label in pairs:
                snap.weather[key] = pairs[label]

    # 4. Tabel riwayat: titik-titik perjalanan + tujuan.
    hist = re.search(r"<table\b[^>]*>(?:(?!</table>).)*?Event(?:(?!</table>).)*?</table>",
                     clean, re.S | re.I)
    if hist:
        snap.history = parse_history(hist.group(0))
        for point in reversed(snap.history):
            if len(point) > 5 and point[5] and not snap.destination:
                snap.destination = point[5]
            if len(point) > 3 and point[3] is not None:
                snap.sog = point[3]
            if len(point) > 4 and point[4] is not None:
                snap.cog = point[4]

    if snap.lat is None:
        raise SourceError("koordinat tidak ditemukan di HTML")

    # Blok "posisi saat ini" sering tertinggal; baris riwayat terbaru lebih segar.
    if snap.history:
        newest = max(snap.history, key=lambda p: p[2])
        if cur_ts is None or newest[2] > cur_ts:
            snap.lat, snap.lon = newest[0], newest[1]
            snap.reported_ts = newest[2]
            snap.reported = time.strftime("%Y-%m-%d %H:%M", time.gmtime(newest[2]))
            if len(newest) > 3 and newest[3] is not None:
                snap.sog = newest[3]
            if len(newest) > 4 and newest[4] is not None:
                snap.cog = newest[4]
    if snap.reported_ts is None:
        snap.reported_ts = cur_ts
    return snap


def parse_history(table_html: str):
    """Baris tabel riwayat -> [lat, lon, ts, sog, cog, tujuan]."""
    points = []
    header = None
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S | re.I):
        cells = [text_of(c) for c in
                 re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if not cells:
            continue
        if header is None:
            joined = " ".join(cells)
            if "Time" in joined and "Event" in joined:
                header = cells
            continue

        def col(name, default):
            for i, cell in enumerate(header):
                if name.lower() in cell.lower():
                    return cells[i] if i < len(cells) else ""
            return cells[default] if default < len(cells) else ""

        ts = parse_dt(col("Time", 0))
        pos = re.search(r"(-?[\d.]+)\s*/\s*(-?[\d.]+)", col("Position", 3))
        if ts is None or not pos:
            continue
        info = col("Info", 4)
        sog = re.search(r"Speed:\s*(-?[\d.]+)", info)
        cog = re.search(r"Course:\s*(-?[\d.]+)", info)
        dest = re.search(r"\[[A-Z]{2}\]\s*([A-Z ]+)|([A-Z]{3}>[A-Z]{3})", col("Position", 3))
        points.append([
            float(pos.group(1)), float(pos.group(2)), ts,
            to_float(sog.group(1)) if sog else None,
            to_float(cog.group(1)) if cog else None,
            (dest.group(1) or dest.group(2)).strip() if dest else "",
        ])
    points.sort(key=lambda p: p[2])
    return points


def fetch_cruisemapper(fetcher: Fetcher, ship) -> Snapshot:
    if not ship.imo:
        raise SourceError("butuh IMO untuk CruiseMapper")
    url = f"https://www.cruisemapper.com/?imo={ship.imo}"
    raw, _ = fetcher.get(url)
    if not raw:
        raise SourceError("halaman kosong")
    snap = Snapshot(source="CruiseMapper")

    m = re.search(r'"map"\s*:\s*\{[^}]*?"lat"\s*:\s*(-?[\d.]+)\s*,\s*'
                  r'"lon"\s*:\s*(-?[\d.]+)', raw)
    if not m:
        raise SourceError("koordinat tidak ditemukan di HTML")
    snap.lat, snap.lon = float(m.group(1)), float(m.group(2))

    for spec in re.finditer(r'<li id="trackerItemSpec_\d+"[^>]*>(.*?)</li>',
                            raw, re.S | re.I):
        seg = spec.group(1)
        lab = re.search(r'class="specLabel"[^>]*>(.*?)</span>', seg, re.S | re.I)
        val = re.search(r'class="specValue"[^>]*>(.*?)</span>', seg, re.S | re.I)
        if not (lab and val):
            continue
        label, value = text_of(lab.group(1)), text_of(val.group(1))
        words = value.split()
        if "Destination" in label:
            snap.destination = value
        elif "Speed" in label and words:
            snap.sog = to_float(words[0])
        elif "Course" in label:
            snap.cog = to_float(value.replace("°", ""))

    rep = re.search(r'id="trackerItemLastReport"[^>]*>(.*?)</', raw, re.S | re.I)
    if rep:
        snap.reported = text_of(rep.group(1))
    return snap


def clean_imo(href: str) -> str:
    """Ambil IMO dari slug, tapi tolak nilai sampahnya.

    Situs menulis beberapa hal yang bukan IMO di segmen itu: kosong (`-imo-`),
    `0`, dan `1073741823` (2**30-1, penanda "tidak ada" yang bocor dari basis
    data). IMO yang sah selalu 7 digit, jadi hanya pola itu yang diterima —
    kalau tidak, panel akan menampilkan "IMO 1073741823" dan cadangan
    CruiseMapper akan menanyakan `?imo=0`.
    """
    m = re.search(r"-imo-(\d+)", href or "")
    return m.group(1) if m and re.fullmatch(r"\d{7}", m.group(1)) else ""


def search_vessels(fetcher: Fetcher, query: str) -> list[dict]:
    """Cari kapal lewat halaman pencarian publik MyShipTracking.

    Mengembalikan daftar kandidat mentah (belum menjadi Ship). `url` diambil
    apa adanya dari href hasil pencarian: urutan slug di sana bisa berbeda dari
    yang disusun vessel_url() (di sana "-mmsi-…-imo-…", di sini "-imo-…-mmsi-…"),
    jadi menebak ulang urutannya hanya menambah risiko salah kapal.
    """
    query = (query or "").strip()
    if len(query) < 3:
        raise SourceError("kata kunci minimal 3 huruf")

    url = ("https://www.myshiptracking.com/vessels?name="
           + urllib.parse.quote_plus(query))
    raw, _ = fetcher.get(url)
    if not raw:
        raise SourceError("halaman pencarian kosong")

    # Tabel hasil punya id tetap. Tabel header ("myst-table-1-head") memuat
    # judul kolom yang sama tapi tidak punya <tbody>, jadi tidak ikut terbaca.
    table = re.search(r'<table[^>]*id="table-filter".*?</table>', raw, re.S | re.I)
    if not table:
        raise SourceError("tabel hasil pencarian tidak ditemukan")

    results = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table.group(0), re.S | re.I):
        link = re.search(r'<a[^>]+href="(/vessels/[^"]+)"[^>]*>(.*?)</a>',
                         row, re.S | re.I)
        if not link:
            continue
        cells = [text_of(c) for c in
                 re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        if len(cells) < 2:
            continue
        href = html_mod.unescape(link.group(1))
        # Bendera hanya ada sebagai title pada ikon; urutan atributnya tidak
        # dijamin, jadi kedua susunan dicoba.
        flag = (re.search(r'class="flag_line"[^>]*title="([^"]*)"', row, re.I)
                or re.search(r'title="([^"]*)"[^>]*class="flag_line"', row, re.I))

        def cell(i):
            return cells[i] if len(cells) > i else ""

        results.append({
            "name": text_of(link.group(2)),
            "mmsi": cell(1),
            "imo": clean_imo(href),
            "url": urllib.parse.urljoin("https://www.myshiptracking.com", href),
            "type": cell(2),
            "area": cell(3),
            "speed": cell(4),
            "destination": cell(5),
            "received": cell(6),
            "flag": html_mod.unescape(flag.group(1)) if flag else "",
        })
    return results


SOURCES = [("MyShipTracking", fetch_myshiptracking),
           ("CruiseMapper", fetch_cruisemapper)]


# ------------------------------------------------------------------- state


@dataclass
class Ship:
    mmsi: str
    label: str = ""
    imo: str = ""
    flag: str = ""
    callsign_hint: str = ""
    url: str = ""            # override URL halaman MyShipTracking

    name: str = ""
    callsign: str = ""
    ship_type: str = ""
    length: float | None = None
    beam: float | None = None
    destination: str = ""
    draught: float | None = None
    area: str = ""
    station: str = ""
    nav_status: str = ""
    trip_distance: str = ""
    avg_speed: str = ""
    max_speed: str = ""
    weather: dict = field(default_factory=dict)

    lat: float | None = None
    lon: float | None = None
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None

    reported: str = ""
    reported_ts: float | None = None
    reported_human: str = ""
    fetched_at: float | None = None
    source: str = ""

    trail: list = field(default_factory=list)
    distance_nm: float = 0.0

    def apply(self, snap: Snapshot):
        for key in ("name", "lat", "lon", "sog", "cog", "heading", "nav_status",
                    "area", "station", "destination", "draught", "callsign",
                    "flag", "length", "beam", "ship_type", "reported",
                    "reported_ts", "reported_human", "trip_distance",
                    "avg_speed", "max_speed"):
            value = getattr(snap, key)
            if value not in (None, ""):
                setattr(self, key, value)
        if snap.weather:
            self.weather = dict(snap.weather)
        self.source = snap.source
        self.fetched_at = time.time()
        self._merge_trail(snap.history)
        if self.lat is not None:
            self._merge_trail([[self.lat, self.lon, int(self.reported_ts or time.time())]])

    def _merge_trail(self, points):
        """Gabungkan titik baru, urut waktu, tanpa duplikat."""
        if not points:
            return
        have = {p[2] for p in self.trail}
        for p in points:
            if p[2] not in have:
                self.trail.append([round(p[0], 5), round(p[1], 5), int(p[2])])
                have.add(p[2])
        self.trail.sort(key=lambda p: p[2])
        if len(self.trail) > 20000:
            del self.trail[: len(self.trail) - 20000]

    def recompute_distance(self):
        total = 0.0
        for a, b in zip(self.trail, self.trail[1:]):
            total += haversine_nm(a[0], a[1], b[0], b[1])
        self.distance_nm = total


class Store:
    def __init__(self, ships_cfg, state_path: Path, max_ships: int = 8):
        self.lock = threading.RLock()
        self.state_path = state_path
        self.max_ships = max_ships
        self.ships: dict[str, Ship] = {}
        for item in ships_cfg:
            mmsi = str(item["mmsi"])
            self.ships[mmsi] = Ship(
                mmsi=mmsi,
                label=item.get("name", ""),
                imo=str(item.get("imo", "")),
                flag=item.get("flag", ""),
                callsign_hint=item.get("callsign", ""),
                url=item.get("url", ""),
            )
        self.status = {
            "state": "starting",     # starting | ok | stale | error
            "last_ok": None,
            "last_try": None,
            "error": "",
            "source": "",
            "polls": 0,
            "failures": 0,
            "mode": "fast",          # fast | idle — diisi poll_loop
            "interval": None,        # jeda NYATA antar-pembaruan tiap kapal (detik)
            "ships": len(self.ships),
            "max_ships": max_ships,
        }
        self.subs: set[queue.Queue] = set()
        self.fetcher: Fetcher | None = None
        self.dirty = False
        self.load()

    # -- persistensi --------------------------------------------------------

    def load(self):
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return
        for mmsi, saved in (data.get("ships") or {}).items():
            ship = self.ships.get(mmsi)
            if not ship:
                continue
            ship.trail = [[float(p[0]), float(p[1]), int(p[2])]
                          for p in saved.get("trail", [])
                          if isinstance(p, list) and len(p) == 3]
            for key in ("name", "callsign", "destination", "area", "nav_status",
                        "station", "reported", "source", "ship_type"):
                if saved.get(key):
                    setattr(ship, key, saved[key])
            for key in ("lat", "lon", "sog", "cog", "length", "beam",
                        "reported_ts", "fetched_at"):
                if saved.get(key) is not None:
                    setattr(ship, key, saved[key])
            ship.recompute_distance()

    def save(self):
        with self.lock:
            payload = {"saved_at": time.time(), "ships": {}}
            for mmsi, s in self.ships.items():
                payload["ships"][mmsi] = {
                    "trail": s.trail,
                    "name": s.name, "callsign": s.callsign,
                    "destination": s.destination, "area": s.area,
                    "nav_status": s.nav_status, "station": s.station,
                    "reported": s.reported, "source": s.source,
                    "ship_type": s.ship_type,
                    "lat": s.lat, "lon": s.lon, "sog": s.sog, "cog": s.cog,
                    "length": s.length, "beam": s.beam,
                    "reported_ts": s.reported_ts, "fetched_at": s.fetched_at,
                }
            self.dirty = False
        tmp = self.state_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.state_path)
        except OSError as exc:
            print(f"[state] gagal menyimpan: {exc}", file=sys.stderr)

    # -- armada: tambah / hapus kapal saat berjalan -------------------------

    def add_ship(self, item: dict) -> str:
        """Tambahkan kapal. Kembalikan "" kalau berhasil, atau alasan gagalnya.

        Sengaja mengembalikan pesan alih-alih melempar exception: pemanggilnya
        adalah HTTP handler yang perlu meneruskan alasan itu apa adanya ke UI
        (duplikat, batas tercapai, MMSI tidak masuk akal).
        """
        mmsi = str(item.get("mmsi") or "").strip()
        if not mmsi.isdigit():
            return "MMSI harus berupa angka"
        # IMO yang tidak 7 digit dibuang, bukan disimpan apa adanya: nilai
        # sampah akan membuat cadangan CruiseMapper menanyakan kapal yang salah.
        imo = str(item.get("imo") or "").strip()
        if not re.fullmatch(r"\d{7}", imo):
            imo = ""
        with self.lock:
            if mmsi in self.ships:
                return f"MMSI {mmsi} sudah dilacak"
            if len(self.ships) >= self.max_ships:
                return (f"batas {self.max_ships} kapal sudah tercapai. Tiap kapal "
                        f"menambah satu permintaan per putaran, dan situs sumber "
                        f"hanya mengizinkan satu permintaan per 5 detik — jadi "
                        f"makin banyak kapal, makin lambat pembaruannya.")
            self.ships[mmsi] = Ship(
                mmsi=mmsi,
                label=str(item.get("name") or "").strip(),
                imo=imo,
                flag=str(item.get("flag") or "").strip(),
                url=str(item.get("url") or "").strip(),
            )
        return ""

    def remove_ship(self, mmsi: str) -> str:
        """Hapus kapal. Kembalikan "" kalau berhasil, atau alasan gagalnya."""
        mmsi = str(mmsi or "").strip()
        with self.lock:
            if mmsi not in self.ships:
                return f"MMSI {mmsi} tidak sedang dilacak"
            if len(self.ships) <= 1:
                return "ini satu-satunya kapal yang dilacak — tidak bisa dikosongkan"
            del self.ships[mmsi]
        return ""

    # -- siaran -------------------------------------------------------------

    def subscribe(self):
        q: queue.Queue = queue.Queue(maxsize=16)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    def broadcast(self):
        payload = self.snapshot(trail_limit=500)
        for q in list(self.subs):
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    # -- serialisasi --------------------------------------------------------

    def snapshot_locked(self, trail_limit=None):
        now = time.time()
        ships = []
        for s in self.ships.values():
            age = None
            if s.reported_ts:
                age = max(0, int(now - s.reported_ts))
            elif s.fetched_at:
                age = max(0, int(now - s.fetched_at))
            ships.append({
                "mmsi": s.mmsi,
                "label": s.label or s.name or s.mmsi,
                "name": s.name,
                "imo": s.imo,
                "callsign": s.callsign or s.callsign_hint,
                "flag": s.flag,
                "type": s.ship_type,
                "length": s.length,
                "beam": s.beam,
                "destination": s.destination,
                "draught": s.draught,
                "area": s.area,
                "station": s.station,
                "nav_status": s.nav_status,
                "trip_distance": s.trip_distance,
                "avg_speed": s.avg_speed,
                "max_speed": s.max_speed,
                "weather": s.weather,
                "lat": s.lat,
                "lon": s.lon,
                "sog": s.sog,
                "sog_kmh": round(s.sog * KNOTS_TO_KMH, 1) if s.sog is not None else None,
                "cog": s.cog,
                "heading": s.heading if s.heading is not None else s.cog,
                "reported": s.reported,
                "reported_human": s.reported_human,
                "source": s.source,
                "age": age,
                "distance_nm": round(s.distance_nm, 1),
                "trail": s.trail if trail_limit is None else s.trail[-trail_limit:],
                "trail_total": len(s.trail),
            })
        # Jumlah kapal dihitung di sini, bukan disalin dari status, supaya tidak
        # pernah basi setelah ada kapal ditambah/dihapus saat berjalan.
        status = dict(self.status)
        status["ships"] = len(self.ships)
        status["max_ships"] = self.max_ships
        return {
            "type": "snapshot",
            "server_time": now,
            "status": status,
            "ships": ships,
        }

    def snapshot(self, trail_limit=None):
        with self.lock:
            return self.snapshot_locked(trail_limit)


def save_config(path: Path, store: Store):
    """Tulis ulang daftar kapal ke ships.json supaya penambahan bertahan
    setelah restart.

    Isi berkas dibaca dulu dan hanya array "ships" yang diganti — kunci lain
    (mis. "_catatan" dan "bbox") dipertahankan apa adanya. Ditulis atomik
    dengan pola yang sama seperti Store.save() supaya berkas tidak pernah
    tertinggal dalam keadaan setengah jadi.
    """
    try:
        cfg = json.loads(path.read_text())
        if not isinstance(cfg, dict):
            cfg = {}
    except (OSError, ValueError):
        cfg = {}

    # Entri asli disimpan per MMSI. Tiap entri baru dimulai dari salinan entri
    # lama, jadi kunci yang ditulis tangan dan tidak dikenal skrip (mis. "note"
    # atau "callsign") tidak ikut terhapus saat berkas ditulis ulang.
    old = {}
    for item in (cfg.get("ships") or []):
        if isinstance(item, dict) and item.get("mmsi") is not None:
            old[str(item["mmsi"])] = item

    with store.lock:
        ships = []
        for s in store.ships.values():
            item = dict(old.get(s.mmsi) or {})
            item["mmsi"] = s.mmsi
            # Hanya field yang benar-benar dipakai pelacakan yang diperbarui.
            # flag/length/beam sengaja TIDAK ditulis ulang: nilai di berkas
            # adalah catatan tangan pemiliknya, bukan sesuatu yang skrip ini
            # berhak menimpanya dengan angka dari situs.
            for key, value in (("name", s.name or s.label), ("imo", s.imo),
                               ("url", s.url)):
                if value:
                    item[key] = value
            ships.append({k: v for k, v in item.items() if v not in ("", None)})
        cfg["ships"] = ships

    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(path)
    except OSError as exc:
        print(f"[config] gagal menyimpan {path}: {exc}", file=sys.stderr)


# ------------------------------------------------------ loop pengambilan data


def fetch_ship(fetcher: Fetcher, ship):
    """Coba tiap sumber berurutan untuk SATU kapal, berhenti di yang berhasil.

    Kembalikan (snapshot, daftar_error). Snapshot None berarti semua sumber gagal.
    """
    errors = []
    for name, fn in SOURCES:
        try:
            return fn(fetcher, ship), errors
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"[{name}] gagal ({ship.label or ship.mmsi}): {exc}",
                  file=sys.stderr)
    return None, errors


def apply_snapshot(store: Store, ship: Ship, snap: Snapshot):
    with store.lock:
        ship.apply(snap)
        ship.recompute_distance()
        store.dirty = True
    print(f"[{snap.source}] {ship.label or ship.mmsi}: "
          f"{snap.lat:.5f}, {snap.lon:.5f} · "
          f"{snap.sog if snap.sog is not None else '?'} kn · "
          f"dilaporkan {snap.reported or snap.reported_human or '?'}")


def poll_ship(store: Store, fetcher: Fetcher, ship) -> bool:
    """Ambil data satu kapal saja. True kalau berhasil.

    Dipakai saat kapal baru ditambahkan: satu permintaan saja, bukan satu
    putaran penuh yang akan menghabiskan waktu N x Crawl-delay.
    """
    snap, _ = fetch_ship(fetcher, ship)
    if snap is None:
        return False
    apply_snapshot(store, ship, snap)
    store.broadcast()
    return True


def poll_once(store: Store, fetcher: Fetcher):
    """Satu putaran penuh: coba semua sumber untuk semua kapal."""
    with store.lock:
        store.status["last_try"] = time.time()
        store.status["polls"] += 1

    any_ok = False
    used_source = ""
    errors = []
    for mmsi, ship in list(store.ships.items()):
        snap, errs = fetch_ship(fetcher, ship)
        errors.extend(errs)
        if snap is None:
            continue
        apply_snapshot(store, ship, snap)
        any_ok = True
        used_source = snap.source

    with store.lock:
        if any_ok:
            store.status.update(state="ok", last_ok=time.time(), error="",
                                source=used_source)
        else:
            store.status.update(state="error", failures=store.status["failures"] + 1,
                                error="; ".join(errors)[:400] or "tidak ada data")
    store.broadcast()


def choose_interval(store: Store, fast: float, idle: float,
                    idle_speed: float) -> tuple[float, str]:
    """Jeda berikutnya: cepat saat ada kapal bergerak, lambat saat semua sandar.

    Saat kapal sandar, halaman sumber praktis tidak berubah, jadi mengecek tiap
    6 detik hanya membebani server tanpa menambah kesegaran. `idle <= 0`
    mematikan mode adaptif (selalu cepat).
    """
    if idle <= 0 or idle <= fast:
        return fast, "fast"
    with store.lock:
        speeds = [s.sog for s in store.ships.values()]
    moving = any(v is not None and v >= idle_speed for v in speeds)
    return (fast, "fast") if moving else (idle, "idle")


def poll_loop(store: Store, fetcher: Fetcher, fast: float, idle: float,
              idle_speed: float, stop: threading.Event,
              wake: threading.Event | None = None):
    # Dengan interval kecil, menulis state.json tiap poll berarti ribuan tulisan
    # berkas per hari untuk data yang isinya sama. Tampilan tetap diperbarui
    # seketika lewat SSE; berkas ini hanya untuk bertahan setelah restart, jadi
    # dibatasi lajunya. Penulisan terakhir tetap dijamin di main().
    #
    # Dengan beberapa kapal, satu putaran bisa jauh lebih lama daripada `wait`
    # karena situs sumber membatasi 1 permintaan per 5 detik. Karena itu jeda
    # diukur dari awal putaran, bukan dari akhirnya.
    last_save = 0.0
    prev_mode = None
    while not stop.is_set():
        round_start = time.time()
        try:
            poll_once(store, fetcher)
        except Exception as exc:  # noqa: BLE001
            print(f"[poll] error tak terduga: {exc}", file=sys.stderr)
        round_secs = time.time() - round_start
        if store.dirty and time.time() - last_save >= SAVE_MIN_INTERVAL:
            store.save()
            last_save = time.time()

        wait, mode = choose_interval(store, fast, idle, idle_speed)
        # Yang dilaporkan ke UI adalah irama NYATA per kapal, bukan angka yang
        # dikonfigurasi. Satu siklus = mengunduh semua kapal (yang tidak bisa
        # lebih rapat daripada Crawl-delay) DITAMBAH jeda sesudahnya — selama
        # jeda itu tidak ada kapal yang dicek. Memakai max() di sini akan
        # meremehkan: dengan 2 kapal, putarannya ~5 detik + jeda 6 detik = 11
        # detik sekali, bukan 6.
        cycle = wait + round_secs
        with store.lock:
            store.status["mode"] = mode
            store.status["interval"] = round(cycle, 1)
        if mode != prev_mode:
            if mode == "idle":
                print(f"[mode] kapal sandar — pengecekan dilonggarkan ke tiap "
                      f"{cycle:g} detik")
            elif prev_mode is not None:
                print(f"[mode] kapal bergerak — pengecekan dirapatkan ke tiap "
                      f"{cycle:g} detik")
            prev_mode = mode

        # Tidur, tapi bangun lebih awal kalau daftar kapal berubah. Tanpa ini,
        # kapal yang baru ditambahkan saat mode sandar tetap menunggu sampai
        # 300 detik habis sebelum iramanya dinilai ulang — padahal kapal
        # bergerak seharusnya langsung dicek rapat.
        if wake is not None:
            wake.wait(wait)
            wake.clear()
        else:
            stop.wait(wait)


# ------------------------------------------------------------- server HTTP


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    store: Store = None
    config_path: Path = None
    wake: threading.Event = None
    index_bytes: bytes = b""

    def log_message(self, *args):
        pass

    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _query(self) -> dict:
        qs = urllib.parse.urlsplit(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload).encode(), "application/json")

    def _api_search(self):
        q = (self._query().get("q") or "").strip()
        try:
            results = search_vessels(self.store.fetcher, q)
        except Exception as exc:  # noqa: BLE001
            self._json(400, {"error": str(exc) or "pencarian gagal"})
            return
        with self.store.lock:
            tracked = set(self.store.ships)
        for r in results:
            r["tracked"] = r["mmsi"] in tracked
        self._json(200, {"query": q, "results": results})

    def _api_add(self):
        q = self._query()
        mmsi = str(q.get("mmsi") or "").strip()
        err = self.store.add_ship(q)
        if err:
            self._json(409, {"ok": False, "error": err})
            return
        save_config(self.config_path, self.store)
        print(f"[armada] + {q.get('name') or mmsi} (MMSI {mmsi})")
        # Isi datanya segera, tapi hanya kapal ini — satu permintaan, bukan
        # satu putaran penuh yang akan memakan N x Crawl-delay.
        with self.store.lock:
            ship = self.store.ships.get(mmsi)
        if ship is not None:
            threading.Thread(target=poll_ship,
                             args=(self.store, self.store.fetcher, ship),
                             daemon=True).start()
        self.store.broadcast()
        # Bangunkan poll_loop supaya irama dinilai ulang sekarang. Tanpa ini,
        # menambah kapal saat mode sandar membuatnya menunggu sampai 300 detik
        # habis sebelum dicek rapat.
        if self.wake is not None:
            self.wake.set()
        self._json(200, {"ok": True, "mmsi": mmsi})

    def _api_remove(self):
        mmsi = (self._query().get("mmsi") or "").strip()
        err = self.store.remove_ship(mmsi)
        if err:
            self._json(409, {"ok": False, "error": err})
            return
        save_config(self.config_path, self.store)
        print(f"[armada] - MMSI {mmsi} berhenti dilacak")
        self.store.broadcast()
        if self.wake is not None:
            self.wake.set()
        self._json(200, {"ok": True, "mmsi": mmsi})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, self.index_bytes, "text/html; charset=utf-8")
        elif path == "/api/state":
            self._send(200, json.dumps(self.store.snapshot()).encode(),
                       "application/json")
        elif path == "/api/search":
            self._api_search()
        elif path == "/api/add":
            self._api_add()
        elif path == "/api/remove":
            self._api_remove()
        elif path == "/api/refresh":
            threading.Thread(target=poll_once,
                             args=(self.store, self.store.fetcher), daemon=True).start()
            self._send(202, b'{"ok":true}', "application/json")
        elif path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = self.store.subscribe()
            try:
                self._sse(self.store.snapshot(trail_limit=500))
                while True:
                    try:
                        payload = q.get(timeout=15)
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self._sse(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                self.store.unsubscribe(q)
        else:
            self._send(404, b"not found", "text/plain")

    def _sse(self, payload: dict):
        data = json.dumps(payload, separators=(",", ":")).encode()
        self.wfile.write(b"data: " + data + b"\n\n")
        self.wfile.flush()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


# -------------------------------------------------------------------- main


def load_config(path: Path):
    if not path.exists():
        sys.exit(f"Config {path} tidak ada.")
    cfg = json.loads(path.read_text())
    ships = cfg.get("ships") or []
    if not ships:
        sys.exit(f"Tidak ada kapal di {path}.")
    return ships


def main():
    # Tanpa ini, stdout ter-buffer saat diarahkan ke berkas dan `tail -f run.log`
    # tidak menampilkan apa pun sampai buffer penuh.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass

    p = argparse.ArgumentParser(description="Pelacak kapal di peta (sumber publik).")
    p.add_argument("--host", default="127.0.0.1",
                   help="alamat bind web (default 127.0.0.1; 0.0.0.0 untuk akses luar)")
    p.add_argument("--port", type=int, default=8777, help="port web (default 8777)")
    p.add_argument("--interval", type=float, default=6.0,
                   help="detik antar-pemeriksaan saat kapal BERGERAK (default 6)")
    p.add_argument("--idle-interval", type=float, default=300.0,
                   help="detik antar-pemeriksaan saat kapal SANDAR/diam "
                        "(default 300). Isi 0 untuk mematikan mode adaptif")
    p.add_argument("--idle-speed", type=float, default=0.5,
                   help="ambang kecepatan (knot) di bawah ini dianggap sandar (default 0.5)")
    p.add_argument("--max-ships", type=int, default=8,
                   help="batas jumlah kapal yang dilacak (default 8). Tiap kapal "
                        "menambah satu permintaan per putaran, dan situs sumber "
                        "hanya mengizinkan satu permintaan per 5 detik")
    p.add_argument("--ships", default=str(HERE / "ships.json"))
    p.add_argument("--state", default=str(HERE / "state.json"))
    args = p.parse_args()

    cfg_path = Path(args.ships)
    store = Store(load_config(cfg_path), Path(args.state), max_ships=args.max_ships)
    if len(store.ships) > args.max_ships:
        print(f"[peringatan] {cfg_path.name} memuat {len(store.ships)} kapal, "
              f"melebihi batas --max-ships {args.max_ships}. Semuanya tetap "
              f"dilacak, tapi penambahan baru akan ditolak.", file=sys.stderr)
    fetcher = Fetcher()
    store.fetcher = fetcher

    Handler.store = store
    Handler.config_path = cfg_path
    Handler.index_bytes = (HERE / "index.html").read_bytes()
    httpd = Server((args.host, args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    shown = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown}:{args.port}"
    for s in store.ships.values():
        print(f"[kapal] {s.label or s.mmsi}  MMSI {s.mmsi}  IMO {s.imo or '-'}")
    if args.idle_interval > args.interval:
        print(f"[cek]   tiap {args.interval:g} detik saat kapal bergerak · "
              f"{args.idle_interval:g} detik saat sandar (adaptif)")
    else:
        print(f"[cek]   tiap {args.interval:g} detik (mode adaptif mati)")
    if len(store.ships) > 1:
        # Jujur sejak awal: situs sumber hanya mengizinkan satu permintaan per
        # 5 detik, jadi dengan N kapal tiap kapal baru diperbarui sekitar
        # (N-1) x 5 detik + jeda — seberapa pun kecilnya --interval.
        n = len(store.ships)
        print(f"[cek]   {n} kapal — situs sumber membatasi {CRAWL_DELAY:g} detik "
              f"antar-permintaan, jadi tiap kapal efektif diperbarui sekitar "
              f"{(n - 1) * CRAWL_DELAY + args.interval:g} detik sekali")
    print()
    print("  " + "=" * (len(url) + 20))
    print(f"   BUKA DI BROWSER:  {url}")
    print("  " + "=" * (len(url) + 20))
    print()

    stop = threading.Event()
    wake = threading.Event()
    Handler.wake = wake
    worker = threading.Thread(target=poll_loop,
                              args=(store, fetcher, args.interval,
                                    args.idle_interval, args.idle_speed, stop, wake),
                              daemon=True)
    worker.start()
    try:
        while worker.is_alive():
            worker.join(timeout=1)
    except KeyboardInterrupt:
        print("\n[app] berhenti…")
    finally:
        stop.set()
        store.save()
        httpd.shutdown()


if __name__ == "__main__":
    main()
