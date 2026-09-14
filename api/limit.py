"""GET /api/limit?max=… — setel berapa banyak kapal yang boleh dilacak.

Nilainya disimpan sebagai kunci "max_ships" di ships.json, jadi ia berlaku
sama di versi lokal maupun di versi Vercel, dan ikut terbawa di riwayat git
seperti perubahan lain.

Halaman yang memanggil ini biasanya TIDAK perlu memicu poller: menaikkan batas
tidak menambah kapal apa pun, dan menurunkannya tidak mengeluarkan kapal yang
sudah ada. Jadi tidak ada `picu_poll_diam_diam()` di sini.
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tracker  # noqa: E402
import vlib  # noqa: E402


class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            vlib.batasi_laju(self)
            q = vlib.query(self)
            n = q.get("max")

            # Aturan rentang dan pesan errornya ada di tracker.set_batas(),
            # dipakai bersama rute /api/limit versi lokal. Tidak ditulis ulang
            # di sini supaya dua mode tidak bisa berbeda diam-diam.
            def ubah(store):
                return tracker.set_batas(store, n)

            vlib.ubah_ships(ubah, f"armada: batas kapal jadi {n}")

            # Dibaca ulang dari hasil commit, bukan dari `n`: kalau ada yang
            # menjepitnya di tengah jalan, yang dilaporkan ke UI harus nilai
            # yang benar-benar tersimpan.
            cfg, _ = vlib.baca_ships()
            vlib.kirim(self, {"ok": True, "max_ships": tracker.batas_dari_cfg(cfg),
                              "ships": len(cfg.get("ships") or [])})
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal menyetel batas"}, 500)
