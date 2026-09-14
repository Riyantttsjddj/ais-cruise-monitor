"""POST /api/buka — periksa kode akses, lalu pasang cookie pembukanya.

Ada DUA rahasia terpisah, dan pemisahan itu disengaja:

  * `KUNCI`       — yang diketik pengguna (6 digit).
  * `NILAI_KUNCI` — token acak panjang, dan itulah isi cookie-nya.

Cookie sengaja BUKAN hash dari kode. Kalau nilainya hash dari 6 digit, siapa
pun yang melihat cookie-nya bisa membaliknya jadi kode aslinya dengan brute
force 10^6 kandidat dalam hitungan milidetik. Token acak yang berdiri sendiri
tidak bisa dibalik sama sekali — dan efek sampingnya menguntungkan: Python dan
JavaScript tidak perlu menghitung hash yang sama persis. Seluruh kelas bug
"dua bahasa menghitung berbeda, semua orang terkunci" hilang.

Cara membuat `NILAI_KUNCI` ada di README (bagian "Kode akses"). Nilainya
sengaja tidak pernah dibuat oleh alat bantu mana pun di repo ini.

Dan satu hal yang diminta pengguna secara eksplisit: **jangan menyimpan
aktivitas login**. Karena itu tidak ada `Max-Age` sama sekali (kedua cookie
sesi, hilang saat peramban ditutup), dan salah satunya — `sekali` — adalah
tiket sekali-pakai yang dihabiskan gerbang begitu satu halaman tersaji. Muat
ulang karena itu selalu menanyakan kodenya lagi. Penjelasannya di `_cookies`.
"""

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vlib  # noqa: E402

# Kodenya 6 digit. Apa pun yang jauh lebih panjang dari ini tidak mungkin benar,
# dan membacanya hanya membuang memori.
BATAS_BODY = 1024


class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            vlib.batasi_laju(self)

            # Dipangkas di KEDUA sisi, dan itu disengaja.
            #
            # Sisi masukan: kode sering tersalin dengan spasi ikut terbawa
            # (terutama dari papan klip di ponsel). Menolaknya membuat pengguna
            # melihat "kode salah" padahal kodenya benar — dan tidak ada
            # petunjuk apa pun bahwa penyebabnya spasi.
            #
            # Sisi env: kalau `KUNCI` di dashboard Vercel tersimpan dengan spasi
            # di ujungnya, TIDAK ADA satu pun kode yang bisa masuk, tanpa gejala
            # apa pun yang menunjuk ke arah sana. Memangkasnya di sini menutup
            # seluruh kelas masalah itu. Kode yang isinya cuma spasi jadi string
            # kosong dan langsung tertangkap pemeriksaan "belum diatur" di atas.
            diharapkan = os.environ.get("KUNCI", "").strip()
            nilai = os.environ.get("NILAI_KUNCI", "").strip()
            if not diharapkan or not nilai:
                # Gagal tertutup, tapi dengan sebab yang jelas. Tanpa pesan ini
                # gejalanya cuma "kode yang benar ditolak" — dan tidak ada
                # petunjuk sama sekali bahwa masalahnya ada di env Vercel.
                raise vlib.ApiError(
                    "KUNCI dan NILAI_KUNCI belum diatur di Environment Variables "
                    "Vercel. Lihat bagian \"Kode akses\" di README.", 500)

            # compare_digest, bukan ==: perbandingan biasa selesai lebih cepat
            # begitu ada karakter pertama yang beda, dan selisih waktunya cukup
            # untuk menebak kode digit demi digit.
            if not hmac.compare_digest(self._baca_kode(), diharapkan):
                # Pesannya seragam — tidak membocorkan seberapa dekat tebakannya,
                # dan tidak membedakan "kode salah" dari "kode kosong".
                raise vlib.ApiError("kode salah", 403)

            vlib.kirim(self, {"ok": True, "pesan": "terbuka"},
                       200, [("Set-Cookie", c) for c in self._cookies(nilai)])
        except vlib.ApiError as exc:
            vlib.kirim(self, {"ok": False, "error": exc.pesan}, exc.kode)
        except Exception as exc:  # noqa: BLE001
            vlib.kirim(self, {"ok": False, "error": str(exc) or "gagal membuka"}, 500)

    def do_GET(self):
        # Endpoint ini menerima kode lewat body, bukan query string — supaya
        # kodenya tidak ikut tercatat di log akses Vercel dan tidak mampir ke
        # riwayat peramban. Permintaan GET ke sini karena itu memang salah.
        vlib.kirim(self, {"ok": False,
                          "error": "pakai POST, kodenya di body"}, 405)

    def _baca_kode(self) -> str:
        try:
            panjang = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return ""
        if panjang <= 0 or panjang > BATAS_BODY:
            return ""
        mentah = self.rfile.read(panjang)
        try:
            isi = json.loads(mentah.decode("utf-8", "replace"))
        except ValueError:
            return ""
        if not isinstance(isi, dict):
            return ""
        return str(isi.get("kode") or "").strip()

    def _cookies(self, nilai: str) -> list:
        # Dua cookie, dan keduanya SENGAJA tanpa `Max-Age`.
        #
        # Tanpa `Max-Age` keduanya jadi cookie SESI: peramban membuangnya saat
        # ditutup, dan tidak ada "aktivitas login" yang tersimpan di perangkat.
        #
        #   `kunci`  — token pembukanya. Ini yang diperiksa gerbang.
        #   `sekali` — tiket sekali-pakai. Gerbang hanya mengizinkan HALAMAN
        #              kalau tiket ini ada, lalu menghapusnya begitu satu halaman
        #              tersaji. Karena itu muat ulang selalu menanyakan kodenya
        #              lagi: cookie `kunci` saja tidak cukup untuk membuka halaman.
        #
        # `Secure` HANYA saat permintaannya benar-benar https. Kalau dipasang
        # mutlak, peramban membuang cookie-nya saat diuji lewat `vercel dev` di
        # http://localhost — jadi alur membukanya tidak bisa diuji lokal sama
        # sekali. Header `x-forwarded-proto` disetel proxy Vercel dan tidak bisa
        # dipalsukan pengunjung, jadi di produksi tidak ada penurunan keamanan.
        #
        # `HttpOnly`  : JavaScript tidak bisa membaca tokennya.
        # `SameSite=Lax`: situs lain tidak bisa memancing permintaan bertoken.
        https = (self.headers.get("x-forwarded-proto") or "").lower() == "https"
        bagian = ["Path=/", "HttpOnly", "SameSite=Lax"]
        if https:
            bagian.append("Secure")
        ekor = "; ".join(bagian)
        return [f"kunci={nilai}; {ekor}", f"sekali=1; {ekor}"]
