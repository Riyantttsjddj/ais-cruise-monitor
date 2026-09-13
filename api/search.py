"""GET /api/search?q=… — cari kapal di MyShipTracking.

Memakai `tracker.search_vessels()` yang sama dengan versi lokal, jadi aturan
"kata kunci minimal 3 huruf" dan bentuk hasilnya tidak ditulis ulang di sini.
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Vercel tidak menjamin akar proyek ada di sys.path saat fungsi dipanggil.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tracker  # noqa: E402
import vlib  # noqa: E402


class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            # Pencarian membebani situs sumber, bukan cuma Vercel. Versi lokal
            # tidak dibatasi karena hanya Anda yang memakainya; versi publik
            # perlu, supaya tidak ada yang bisa menjadikan ini alat pembanjiri
            # permintaan ke MyShipTracking.
            vlib.batasi_laju(self)
            q = (vlib.query(self).get("q") or "").strip()
            hasil = tracker.search_vessels(tracker.Fetcher(), q)
            terlacak = vlib.ships_terlacak()
            for r in hasil:
                r["tracked"] = r["mmsi"] in terlacak
            vlib.kirim(self, {"query": q, "results": hasil})
        except vlib.ApiError as exc:
            vlib.kirim(self, {"error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            # Sama seperti versi lokal: kegagalan pencarian dilaporkan sebagai
            # 400 dengan pesannya apa adanya, bukan 500 generik.
            vlib.kirim(self, {"error": str(exc) or "pencarian gagal"}, 400)
