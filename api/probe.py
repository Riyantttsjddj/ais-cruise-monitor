"""GET /api/probe — periksa apakah fungsi Vercel ini benar-benar bisa bekerja.

Sekali pakai, untuk diagnosis setelah deploy. Menjawab tiga pertanyaan yang
menentukan apakah endpoint tulis akan berfungsi, dan yang tidak bisa saya
pastikan dari sini:

  1. Apakah `import tracker` berhasil? Runtime Python Vercel seharusnya
     menyertakan seluruh berkas proyek, jadi `tracker.py` ikut terbawa — tapi
     itu perlu dibuktikan, bukan diasumsikan.
  2. Apakah GH_TOKEN dan GH_REPO terbaca, dan apakah tokennya benar-benar boleh
     menulis ke repo itu? Yang diuji adalah izin yang dimiliki TOKEN-nya, bukan
     izin Anda sebagai pemilik repo — dua hal itu berbeda, dan yang pertama
     yang menentukan. Lihat catatan di bagian 2 di bawah.
  3. Apakah IP Vercel diterima MyShipTracking? Kalau Cloudflare menantangnya,
     pencarian dari UI tidak akan pernah berhasil.

Balasannya sengaja tidak memuat nilai tokennya — hanya ada/tidak, panjangnya,
dan awalan empat huruf, yang cukup untuk memastikan yang tersalin benar tanpa
membocorkan rahasianya ke peramban.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vlib  # noqa: E402


def _kode(method: str, path: str, payload: dict | None = None) -> int:
    """Kode HTTP mentah dari GitHub, tanpa melempar. 0 kalau bukan HTTPError.

    Dipakai untuk menguji IZIN tanpa efek samping: badan yang sengaja tidak
    lengkap dikirim ke jalur yang tidak ada. GitHub memeriksa izin lebih dulu
    daripada keberadaan — sudah dibuktikan, jalur yang tidak ada pun menjawab
    403 kalau tokennya tidak berizin. Jadi 403/401 berarti izinnya kurang, dan
    kode lain (404/422/409) berarti izinnya ada tapi badannya memang salah.

    Hasilnya: tidak ada commit dan tidak ada run yang pernah dibuat.
    """
    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GH_REPO", "").strip()
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/{path}",
        data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:  # noqa: BLE001
        return 0


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
                kred["repo"] = info.get("full_name")
                kred["baca"] = True
            except vlib.ApiError as exc:
                kred["baca"] = False
                kred["error"] = exc.pesan

            # PENTING: jangan memakai `permissions.push` dari balasan
            # `/repos/<pemilik>/<repo>` untuk menyimpulkan kemampuan token.
            # Field itu melaporkan izin PENGGUNA terhadap repo — dan pemilik
            # repo selalu `push: true` — bukan izin yang diberikan KEPADA
            # tokennya. Versi pertama probe ini memakainya dan karena itu
            # menjawab "semua siap" untuk token yang sebenarnya read-only.
            kode_tulis = _kode("PUT", "contents/.probe-izin.json", {})
            kred["boleh_menulis"] = kode_tulis not in (0, 401, 403)
            if not kred["boleh_menulis"]:
                kred["catatan_tulis"] = (
                    f"Contents TIDAK bisa ditulis (HTTP {kode_tulis}). Ubah "
                    "tokennya jadi Contents: Read and write.")

            kode_micu = _kode(
                "POST", "actions/workflows/.probe-tidak-ada.yml/dispatches",
                {"ref": "main"})
            kred["boleh_memicu"] = kode_micu not in (0, 401, 403)
            if not kred["boleh_memicu"]:
                kred["catatan_micu"] = (
                    f"Actions TIDAK bisa memicu workflow (HTTP {kode_micu}). "
                    "Ubah tokennya jadi Actions: Read and write.")

            kred["ok"] = bool(kred.get("baca") and kred["boleh_menulis"]
                              and kred["boleh_memicu"])
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
