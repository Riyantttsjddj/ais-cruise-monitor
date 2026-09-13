"""GET /api/probe — periksa apakah fungsi Vercel ini benar-benar bisa bekerja.

Sekali pakai, untuk diagnosis setelah deploy. Menjawab tiga pertanyaan yang
menentukan apakah endpoint tulis akan berfungsi, dan yang tidak bisa saya
pastikan dari sini:

  1. Apakah `import tracker` berhasil? Runtime Python Vercel seharusnya
     menyertakan seluruh berkas proyek, jadi `tracker.py` ikut terbawa — tapi
     itu perlu dibuktikan, bukan diasumsikan.
  2. Apakah GH_TOKEN dan GH_REPO terbaca, dan apakah tokennya benar-benar boleh
     menulis ke repo itu?
  3. Apakah IP Vercel diterima MyShipTracking? Kalau Cloudflare menantangnya,
     pencarian dari UI tidak akan pernah berhasil.

Balasannya sengaja tidak memuat nilai tokennya — hanya ada/tidak, panjangnya,
dan awalan empat huruf, yang cukup untuk memastikan yang tersalin benar tanpa
membocorkan rahasianya ke peramban.
"""

import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vlib  # noqa: E402


class handler(BaseHTTPRequestHandler):

    def log_message(self, *args):
        pass

    def do_GET(self):
        laporan: dict = {}

        # 1. Modul inti terbawa ke fungsi ini?
        try:
            import tracker
            laporan["modul"] = {
                "ok": True,
                "berkas": str(Path(tracker.__file__).name),
                "user_agent": tracker.USER_AGENT[:50],
            }
        except Exception as exc:  # noqa: BLE001
            laporan["modul"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # 2. Kredensial GitHub.
        token = os.environ.get("GH_TOKEN", "").strip()
        repo = os.environ.get("GH_REPO", "").strip()
        kred: dict = {
            "GH_TOKEN": f"ada ({len(token)} huruf, awalan {token[:4]}…)"
                        if token else "TIDAK ADA",
            "GH_REPO": repo or "TIDAK ADA",
        }
        if token and repo:
            try:
                info = vlib._gh("GET", "")
                izin = (info.get("permissions") or {})
                kred["ok"] = bool(izin.get("push"))
                kred["repo"] = info.get("full_name")
                kred["boleh_menulis"] = bool(izin.get("push"))
                if not izin.get("push"):
                    kred["catatan"] = ("token terbaca tapi TIDAK punya izin tulis. "
                                       "Ubah tokennya jadi Contents: Read and write.")
            except vlib.ApiError as exc:
                kred["ok"] = False
                kred["error"] = exc.pesan
        else:
            kred["ok"] = False
        laporan["github"] = kred

        # 3. Situs sumber dari IP Vercel.
        try:
            req = urllib.request.Request(
                "https://www.myshiptracking.com/",
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                       "AppleWebKit/537.36 Chrome/120 Safari/537.36"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
                laporan["sumber"] = {
                    "ok": resp.status == 200 and b"myshiptracking" in body.lower(),
                    "status": resp.status,
                    "byte": len(body),
                    "server": resp.headers.get("server", ""),
                }
        except urllib.error.HTTPError as exc:
            laporan["sumber"] = {"ok": False, "status": exc.code,
                                 "error": f"HTTP {exc.code} {exc.reason}",
                                 "server": exc.headers.get("server", "")}
        except Exception as exc:  # noqa: BLE001
            laporan["sumber"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        semua_ok = all(v.get("ok") for v in laporan.values() if isinstance(v, dict))
        laporan["ringkas"] = ("semua siap" if semua_ok
                              else "ADA YANG BELUM BERES — lihat bagian yang ok:false")
        vlib.kirim(self, laporan, 200 if semua_ok else 500)
