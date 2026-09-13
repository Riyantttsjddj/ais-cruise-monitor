"""GET /api/refresh — minta pengecekan sekarang (tombol 🔄 Cek).

Di versi lokal ini memanggil `poll_once()` di sebuah thread. Di Vercel tidak ada
thread yang boleh hidup melewati balasan, dan pengecekannya butuh ~20-40 detik —
jauh melewati anggaran waktu yang nyaman untuk sebuah klik. Jadi yang dilakukan
di sini adalah **meminta GitHub Actions menjalankannya**, lalu UI menunggu
`fetched_at` di data.json berubah.

Konsekuensinya jujur: tombol ini tidak instan. Ia meminta, bukan mengerjakan.
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
            vlib.picu_poll()
            vlib.kirim(self, {"ok": True, "diminta": True}, 202)
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal meminta"}, 500)
