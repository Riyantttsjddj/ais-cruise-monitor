"""Utilitas bersama untuk fungsi-fungsi Vercel di `api/`.

Versi lokal menyimpan daftar kapal di `ships.json` pada disk dan jejak lintasan
di `state.json`. Di Vercel tidak ada disk yang bertahan, jadi:

  * daftar kapal  → tinggal di repo (branch `main`), ditulis lewat GitHub
                    Contents API. Itu juga yang membuat perubahan dari UI
                    tercatat di riwayat git dan bisa dibalik.
  * jejak lintasan → TIDAK ditangani di sini. Itu urusan workflow `poll.yml`,
                    yang menyimpannya di branch `data`.

Modul ini sengaja ditaruh di akar proyek, bukan di dalam `api/`: setiap berkas
`.py` di dalam `api/` menjadi endpoint tersendiri, jadi menaruh kode bersama di
sana akan memunculkan rute yang tidak diinginkan.
"""

import base64
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Runtime Python Vercel menjalankan berkas ini dari akar proyek, tapi `sys.path`
# tidak dijamin memuat akar itu. Tanpa baris ini, `import tracker` bisa gagal
# tergantung bagaimana fungsi dipanggil.
AKAR = Path(__file__).resolve().parent
if str(AKAR) not in sys.path:
    sys.path.insert(0, str(AKAR))

import tracker  # noqa: E402  (harus setelah sys.path diatur)

# `ships.json` disimpan di akar repo, di branch `main`.
SHIPS_PATH = "ships.json"
BRANCH = "main"
WORKFLOW = "poll.yml"


class ApiError(Exception):
    """Kesalahan yang aman ditampilkan ke pengguna, dengan kode HTTP."""

    def __init__(self, pesan: str, kode: int = 400):
        super().__init__(pesan)
        self.pesan = pesan
        self.kode = kode


# ------------------------------------------------------------ HTTP helpers


def kirim(handler, payload: dict, kode: int = 200):
    """Kirim balasan JSON.

    Sengaja TIDAK ada header `Access-Control-Allow-Origin`. Fungsi ini
    se-origin dengan UI-nya (keduanya di domain Vercel yang sama), jadi CORS
    tidak dibutuhkan — dan tanpa header itu, browser di situs lain tidak bisa
    memanggil endpoint tulis ini. Itu penjagaan gratis yang layak dipertahankan;
    jangan tambahkan `*` tanpa alasan yang jelas.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(kode)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def query(handler) -> dict:
    qs = urllib.parse.urlsplit(handler.path).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(qs).items()}


# --------------------------------------------------------- pembatas laju

_LAJU: dict[str, list[float]] = {}
_LAJU_LOCK = threading.Lock()

# Per menit, per IP, hanya untuk endpoint yang menulis (add/remove/refresh).
# Endpoint baca tidak dibatasi.
LAJU_MAKS = 12


def batasi_laju(handler, batas: int = LAJU_MAKS, jendela: float = 60.0):
    """Pembatas laju sederhana. Lempar ApiError kalau terlampaui.

    JUJUR SOAL BATASNYA: ini disimpan di memori proses, dan Vercel bisa
    menjalankan beberapa instans fungsi sekaligus lalu mematikannya kapan saja.
    Jadi ini BUKAN pembatas yang bisa diandalkan — ia hanya menahan tombol yang
    ditekan bertubi-tubi dari satu instans yang sedang panas.

    Penjaga yang benar-benar bekerja adalah `max_ships` di Store.add_ship():
    penambahan ke-9 ditolak, jadi tidak ada yang bisa membanjiri repo dengan
    ribuan kapal. Ini hanya mengurangi derau di riwayat commit.
    """
    ip = (handler.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if not ip:
        ip = handler.client_address[0] if handler.client_address else "?"
    kini = time.monotonic()
    with _LAJU_LOCK:
        jejak = [t for t in _LAJU.get(ip, []) if kini - t < jendela]
        if len(jejak) >= batas:
            _LAJU[ip] = jejak
            raise ApiError("terlalu banyak permintaan — coba lagi sebentar lagi", 429)
        jejak.append(kini)
        _LAJU[ip] = jejak
        # Buang IP yang sudah tidak aktif supaya dict-nya tidak tumbuh terus di
        # instans yang hidup lama.
        if len(_LAJU) > 500:
            for k in [k for k, v in _LAJU.items() if not v or kini - v[-1] > jendela]:
                _LAJU.pop(k, None)


# ------------------------------------------------------------ GitHub API


def _gh(method: str, path: str, payload: dict | None = None) -> dict:
    """Panggil GitHub REST API. Lempar ApiError kalau gagal."""
    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GH_REPO", "").strip()
    if not token or not repo:
        raise ApiError(
            "GH_TOKEN dan GH_REPO belum diatur di Environment Variables Vercel. "
            "Lihat bagian \"Pasang di Vercel\" di README.", 500)

    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": tracker.USER_AGENT,
        })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise ApiError("GH_TOKEN ditolak GitHub (401). Token mungkin sudah "
                           "kedaluwarsa atau salah salin.", 502)
        if exc.code == 403:
            raise ApiError("GitHub menolak (403). Tokennya perlu izin "
                           "Contents: Read and write pada repo ini.", 502)
        raise ApiError(f"GitHub API {exc.code}: {detail}", 502)
    except urllib.error.URLError as exc:
        raise ApiError(f"tidak bisa menghubungi GitHub: {exc.reason}", 502)


def baca_ships() -> tuple[dict, str]:
    """Baca ships.json dari repo. Kembalikan (isi, sha)."""
    d = _gh("GET", f"contents/{SHIPS_PATH}?ref={BRANCH}")
    try:
        isi = base64.b64decode(d["content"]).decode("utf-8")
        cfg = json.loads(isi)
    except (KeyError, ValueError) as exc:
        raise ApiError(f"ships.json di repo tidak terbaca: {exc}", 502)
    if not isinstance(cfg, dict):
        cfg = {}
    return cfg, d.get("sha", "")


def tulis_ships(cfg: dict, sha: str, pesan: str):
    """Commit ships.json lewat Contents API."""
    isi = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    _gh("PUT", f"contents/{SHIPS_PATH}", {
        "message": pesan,
        "content": base64.b64encode(isi.encode("utf-8")).decode(),
        "sha": sha,
        "branch": BRANCH,
    })


def ubah_ships(ubah, pesan: str) -> dict:
    """Baca ships.json, jalankan `ubah(store)`, lalu commit hasilnya.

    `ubah` menerima Store dan mengembalikan pesan error ("" kalau berhasil).
    Store dibangun dari isi berkas di repo, jadi validasi MMSI, penolakan
    duplikat, dan batas `max_ships` semuanya memakai kode yang sama dengan versi
    lokal — tidak ada aturan yang ditulis dua kali dan bisa berbeda diam-diam.

    sha dari Contents API berubah setiap kali berkas ditulis. Dua permintaan
    yang hampir bersamaan karena itu bisa bertabrakan (409). Sekali percobaan
    ulang dengan sha yang segar sudah cukup untuk itu, dan jauh lebih baik
    daripada gagal begitu saja saat dua orang menekan tombol berdekatan.
    """
    for percobaan in range(2):
        cfg, sha = baca_ships()
        store = Store_dari(cfg)
        err = ubah(store)
        if err:
            raise ApiError(err, 409)
        cfg = tracker.config_payload(cfg, store)
        try:
            tulis_ships(cfg, sha, pesan)
            return cfg
        except ApiError as exc:
            if "409" in exc.pesan and percobaan == 0:
                continue
            raise
    raise ApiError("repo sedang sibuk — coba lagi", 409)


def Store_dari(cfg: dict):
    """Bangun Store dari isi ships.json, tanpa berkas state.

    `state_path` sengaja menunjuk berkas yang tidak ada: Store.load() langsung
    keluar kalau berkasnya tidak ada, dan di sini jejak lintasan memang bukan
    urusan kita.
    """
    ships = [s for s in (cfg.get("ships") or [])
             if isinstance(s, dict) and s.get("mmsi") is not None]
    if not ships:
        raise ApiError("ships.json di repo tidak memuat kapal apa pun", 500)
    return tracker.Store(ships, Path("/tmp/tidak-ada-state.json"))


def picu_poll():
    """Minta GitHub menjalankan workflow poll sekarang (tombol 🔄 Cek).

    Dipakai juga setelah kapal ditambah/dihapus. Versi lokal langsung mengecek
    kapal baru satu kali saat ditambahkan; tanpa ini, versi Vercel akan
    menampilkan kapal itu baru sampai 5 menit kemudian — dan kapal yang baru
    dihapus masih ikut tampil selama itu.

    Kegagalan di sini sengaja tidak dianggap kegagalan: kalau tokennya tidak
    punya izin Actions, menambah kapal tetap berhasil dan cuma menunggu jadwal
    berikutnya. Itu jauh lebih baik daripada menolak perubahan yang sah.
    """
    _gh("POST", f"actions/workflows/{WORKFLOW}/dispatches", {"ref": BRANCH})


def picu_poll_diam_diam():
    """picu_poll() yang tidak melempar — untuk dipanggil setelah perubahan."""
    try:
        picu_poll()
        return True
    except ApiError:
        return False


def ships_terlacak() -> set:
    """Himpunan MMSI yang sedang dilacak, untuk menandai hasil pencarian."""
    try:
        cfg, _ = baca_ships()
    except ApiError:
        # Pencarian tetap berguna walau daftar kapal sedang tidak terbaca;
        # lebih baik hasilnya tidak bertanda "sudah ada" daripada gagal total.
        return set()
    return {str(s["mmsi"]) for s in (cfg.get("ships") or [])
            if isinstance(s, dict) and s.get("mmsi") is not None}
