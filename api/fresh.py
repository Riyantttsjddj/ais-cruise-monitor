"""`GET /api/fresh?sejak=<sha>` — apakah run poller sudah menghasilkan data baru?

KENAPA INI ADA. Tombol 🔄 Cek di versi Vercel dulu menunggu data baru dengan
membaca `data.json` dari `raw.githubusercontent.com` berulang-ulang sambil
menambahkan `?t=` sebagai cache-buster. Itu **tidak pernah bisa berhasil**.

`raw.githubusercontent.com` mengabaikan query string saat menentukan cache
key-nya. Terukur di repo ini: dua URL dengan `?t=` yang berbeda mengembalikan
`etag` yang identik (`x-cache: HIT`), dan `cache-control: max-age=300` membuat
salinan yang sama dilayani basi sampai lima menit. Yang dikalahkan `?t=` cuma
cache peramban, bukan cache CDN-nya. Akibatnya tombolnya menunggu 2,5 menit
sia-sia lalu menampilkan pesan yang menuduh runner GitHub lambat — padahal
runner-nya selesai dalam ~20 detik dan datanya sudah ada di branch `data` sejak
tadi.

GitHub API tidak punya masalah itu, dan repo ini sudah punya `GH_TOKEN`.
Satu panggilan mengembalikan commit terakhir branch `data`; kalau sha-nya
berbeda dari `sejak`, run barunya sudah selesai — dan isinya ikut dikirim sekali
itu juga, supaya UI tidak perlu menyentuh CDN sama sekali.

Saat belum ada perubahan balasannya sengaja kecil (sha saja, tanpa isi), karena
ini dipanggil berulang tiap beberapa detik selama tombolnya menunggu.
"""

import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vlib  # noqa: E402  (harus setelah sys.path diatur)


class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            sejak = (vlib.query(self).get("sejak") or "").strip()
            sha = vlib.sha_data()
            # Tanpa `sejak` tidak ada yang bisa dibandingkan — balas sha-nya saja
            # supaya pemanggil berikutnya punya titik acuan.
            baru = bool(sejak) and sha != sejak
            out = {"ok": True, "sha": sha, "baru": baru}
            if baru:
                out["data"] = vlib.baca_data()
            vlib.kirim(self, out)
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal memeriksa"}, 500)
