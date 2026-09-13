"""GET /api/remove?mmsi=… — berhenti melacak satu kapal."""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vlib  # noqa: E402


class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            vlib.batasi_laju(self)
            mmsi = (vlib.query(self).get("mmsi") or "").strip()

            # Termasuk penolakan menghapus kapal terakhir — itu aturan di
            # Store.remove_ship(), bukan aturan yang ditulis ulang di sini.
            vlib.ubah_ships(lambda store: store.remove_ship(mmsi),
                            f"armada: - MMSI {mmsi} berhenti dilacak")
            # Tanpa ini, kapal yang baru dihapus masih ikut tampil di peta
            # sampai jadwal polling berikutnya menulis ulang data.json.
            vlib.picu_poll_diam_diam()
            vlib.kirim(self, {"ok": True, "mmsi": mmsi})
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal menghapus"}, 500)
