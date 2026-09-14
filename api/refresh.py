"""GET /api/refresh — minta pengecekan sekarang (tombol 🔄 Cek).

Di versi lokal ini memanggil `poll_once()` di sebuah thread. Di Vercel tidak ada
thread yang boleh hidup melewati balasan, dan pengecekannya butuh ~20-40 detik —
jauh melewati anggaran waktu yang nyaman untuk sebuah klik. Jadi yang dilakukan
di sini adalah **meminta GitHub Actions menjalankannya**, lalu UI menunggu
stempel data berubah.

Untuk menunggu itu UI memakai `/api/fresh`, BUKAN `raw.githubusercontent.com`:
CDN raw mengabaikan query string saat menentukan cache key, jadi cache-buster
`?t=` tidak menembusnya dan berkas yang sama dilayani basi sampai 5 menit.
Karena itu balasan di sini menyertakan `sha` branch `data` **sebelum** run
dipicu — itulah titik acuan yang dibandingkan UI. `/api/fresh` mengurus sisanya.

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
            # Sha dibaca SEBELUM run dipicu, bukan sesudah. Kalau sesudah, run
            # yang keburu selesai di antara kedua panggilan itu akan
            # menghasilkan sha yang sudah baru — dan UI lalu menunggu perubahan
            # yang tidak akan pernah datang, persis gejala yang sedang
            # diperbaiki di sini.
            try:
                sha = vlib.sha_data()
            except vlib.ApiError:
                # Jangan gagalkan permintaan hanya karena titik acuannya tidak
                # terbaca. /api/refresh juga dipanggil diam-diam oleh
                # jagaKesegaran() di UI; kalau ia mulai gagal, penyembuhan
                # sendiri halaman itu ikut mati. UI menangani sha kosong.
                sha = ""
            vlib.picu_poll()
            vlib.kirim(self, {"ok": True, "diminta": True, "sha": sha}, 202)
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal meminta"}, 500)
