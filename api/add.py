"""GET /api/add?mmsi=…&name=…&imo=…&url=… — tambah kapal.

Di versi lokal handler ini memanggil `store.add_ship()` lalu `save_config()`.
Di sini bagian pertamanya sama persis; bagian keduanya diganti commit ke
GitHub, karena tidak ada disk yang bertahan.
"""

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
            q = vlib.query(self)
            mmsi = str(q.get("mmsi") or "").strip()
            nama = q.get("name") or mmsi

            # Validasi MMSI, penolakan duplikat, dan batas `max_ships` semuanya
            # milik Store.add_ship() — tidak ada salinannya di sini.
            vlib.ubah_ships(lambda store: store.add_ship(q),
                            f"armada: + {nama} (MMSI {mmsi})")
            # Supaya kapal barunya muncul di peta cepat, bukan menunggu jadwal
            # berikutnya. Gagal pun tidak apa-apa.
            vlib.picu_poll_diam_diam()
            vlib.kirim(self, {"ok": True, "mmsi": mmsi})
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal menambahkan"}, 500)
