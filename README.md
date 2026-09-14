# Pelacak Kapal Pesiar

Peta pelacakan posisi kapal yang tampil di browser seperti GPS, dengan posisi
yang diperbarui otomatis. Bawaannya **Wonder of the Seas** (IMO `9838345`,
MMSI `311001033`), tetapi kapal lain bisa ditambah dan dihapus langsung dari
antarmuka — lihat "Menambah kapal lain" di bawah.

```
python3 tracker.py
```

Lalu buka **http://127.0.0.1:8777** di browser.

Bisa juga dijalankan di Vercel sebagai situs publik. Itu **bukan** pemindahan
yang setara — ada yang hilang, dan bagian "Menjalankan di Vercel" di bawah
menyebutkannya terus terang sebelum Anda memutuskan.

---

## Penting dibaca dulu: data ini bukan GPS real-time

Skrip ini **menyalin (scrape) halaman publik** MyShipTracking dan CruiseMapper —
tidak ada API key, tidak ada langganan. Konsekuensinya:

- **Posisi bisa tertinggal beberapa jam.** Kapal hanya memancarkan posisi baru
  secara berkala, dan situs sumber menampilkannya dengan jeda tambahan. Saat
  kapal bersandar (seperti sekarang), update bisa berhenti berjam-jam.
  Panel kanan menampilkan umur data (`age`) — percayai angka itu.
- **Bukan untuk navigasi atau keputusan operasional.** Untuk itu pakai
  penyedia berbayar dengan AIS satelit.
- **Bisa rusak sewaktu-waktu.** Kalau situs sumber mengubah struktur HTML-nya,
  parser akan gagal. Skrip akan melaporkan error di panel status, bukan
  diam-diam menampilkan data lama — tapi perbaikannya harus manual
  (lihat "Kalau parser rusak (versi lokal)" di bawah).
- **Halaman `?mmsi=` tidak dipakai.** Halaman itu tidak memuat koordinat sama
  sekali. Skrip menyusun URL slug dari nama kapal:
  `/vessels/wonder-of-the-seas-imo-9838345-mmsi-311001033`.

### Kesopanan terhadap situs sumber

- Throttle per-host **5 detik** (`CRAWL_DELAY`), sesuai `Crawl-delay` di
  `robots.txt` MyShipTracking. Interval default 6 detik tetap di atas batas ini.
- Kedua situs mengizinkan halaman ini di `robots.txt` mereka (CruiseMapper
  hanya melarang `/admin/`).

**Koreksi penting soal cache.** Kode ini memang mengirim permintaan kondisional
(`If-None-Match` / `If-Modified-Since`), tetapi **MyShipTracking tidak
mengirim `ETag` maupun `Last-Modified`** — header-nya justru memasang
`Cache-Control: no-store, no-cache, must-revalidate`. Artinya jalur `304`
tidak pernah aktif di sini dan **setiap pengecekan mengunduh halaman penuh
~115 KB**.

Karena itu polling dibuat **adaptif** — cepat saat ada gunanya, lambat saat
tidak. Saat kapal sandar, halaman sumber praktis tidak berubah, jadi mengecek
tiap 6 detik hanya membebani server tanpa menambah kesegaran:

| Kondisi | Interval | Pengecekan/hari | Unduhan/hari |
|---|---|---|---|
| Kapal bergerak (> 0,5 kn) | 6 detik | ~14.400 | ~1,6 GB |
| Kapal sandar | 300 detik | ~290 | ~33 MB |

Ambang "bergerak" diatur `--idle-speed` (default 0,5 kn). Konsekuensi yang
perlu diketahui: saat kapal baru mulai bergerak dari posisi sandar, Anda bisa
menunggu **hingga 5 menit** sebelum script menyadarinya dan beralih ke mode
cepat. Setelah itu pembaruannya rapat kembali.

**Kalau kapalnya lebih dari satu, iramanya melambat — dan itu wajar.** Throttle
5 detik membatasi *jarak antar-permintaan*, bukan tiap permintaan. Satu putaran
berisi N permintaan (satu per kapal), jadi:

```
irama nyata tiap kapal  ≈  (N − 1) × 5 detik  +  interval
```

| Jumlah kapal | Irama saat bergerak | Irama saat sandar |
|---|---|---|
| 1 | 6 detik | 300 detik |
| 2 | ~11 detik | ~305 detik |
| 4 | ~21 detik | ~315 detik |
| 8 (batas default) | ~41 detik | ~335 detik |

Angka ini **bukan taksiran** — skrip mengukur durasi putaran yang sebenarnya
dan menampilkannya di pil status. Kalau tertulis "tiap 21 s", memang segitu
kenyataannya. Tombol **➕ Tambah kapal** menolak permintaan ke-9 dan menyebut
batasnya, justru supaya angka di tabel ini tidak diam-diam membengkak.

Batasnya bisa diubah lewat `--max-ships`, tetapi ketahuilah konsekuensinya:
20 kapal berarti satu putaran minimal 95 detik, dan situs sumber menerima
20 permintaan penuh (~2,3 MB) tiap putaran itu.

Kalau mau selalu rapat tanpa adaptif: `--idle-interval 0`.
Kalau mau hemat penuh (mis. untuk pantauan jangka panjang): `--interval 90 --idle-interval 600`.

Tombol **🔄 Cek** di antarmuka memaksa pengecekan kapan saja tanpa menunggu
jadwal — berguna justru saat mode sandar sedang longgar.

---

## Opsi

| Opsi | Default | Keterangan |
|---|---|---|
| `--host` | `127.0.0.1` | Alamat bind. Pakai `0.0.0.0` kalau mau diakses dari HP di jaringan yang sama. |
| `--port` | `8777` | Port HTTP. |
| `--interval` | `6` | Jeda antar-poll (detik) saat kapal **bergerak** |
| `--idle-interval` | `300` | Jeda saat kapal **sandar**. Isi `0` untuk mematikan mode adaptif |
| `--idle-speed` | `0.5` | Ambang kecepatan (knot); di bawah ini dianggap sandar |
| `--max-ships` | `8` | Jumlah maksimum kapal yang boleh dilacak |
| `--ships` | `ships.json` | Berkas konfigurasi kapal. |
| `--state` | `state.json` | Berkas penyimpanan jejak lintasan. |

Hanya butuh **Python 3.10+ standar** — tanpa `pip install` apa pun.

---

## Menambah kapal lain

Klik **➕ Tambah kapal** di panel, lalu ketik minimal 3 huruf nama kapal dan
tekan **Cari**. Hasilnya datang dari halaman pencarian MyShipTracking: nama,
MMSI, tipe, perairan, dan bendera. Klik **+** pada baris yang benar.

Perhatikan **MMSI**-nya, jangan cuma namanya — kapal pesiar sering punya nama
mirip (Wonder / Oasis / Allure of the Seas). Kapal yang sudah dilacak ditandai
"sudah ada" dan tombolnya mati.

Tidak menemukan kapalnya? Buka **Tambah manual** di bawah hasil pencarian dan
isi MMSI (wajib), IMO, dan nama.

**MMSI 9 digit, IMO 7 digit — jangan tertukar.** Ini kesalahan yang paling
mudah terjadi: menyalin IMO ke kolom MMSI. Sekarang ditolak dengan pesan yang
menjelaskannya. Sebelumnya nilai 7 digit itu **diterima diam-diam** sebagai
kapal hantu yang tidak pernah punya posisi — UI tetap bilang "Mulai melacak",
dan tidak ada satu pun petunjuk kenapa kapalnya tidak muncul di peta.

Jalur manual tidak memverifikasi apa pun ke situs sumber — kapal baru muncul di
peta begitu pengecekan pertama berhasil, dan kalau MMSI-nya salah (tapi tetap 9
digit) panelnya akan melaporkan error, bukan posisi palsu.

Kapal baru **langsung dicek satu kali** saat ditambahkan, dan polling yang
sedang menunggu dibangunkan supaya iramanya dihitung ulang saat itu juga.

### Menghapus kapal

Tombol **×** di baris kapal. Karena sekali sentuh bisa keliru, tombolnya harus
ditekan **dua kali** — yang pertama mengubahnya jadi "Yakin?" selama 3 detik.
Jejak lintasan kapal itu ikut hilang dari `state.json`.

Kapal terakhir tidak bisa dihapus. Peta tanpa kapal tidak ada gunanya, dan
tidak ada jalan di antarmuka untuk menambahkannya kembali kalau daftarnya
kosong.

### Lewat `ships.json`

Antarmuka menulis balik ke `ships.json` setiap kali kapal ditambah atau
dihapus, jadi daftarnya bertahan setelah restart. Anda tetap boleh mengeditnya
langsung; `mmsi` wajib, sisanya opsional sebagai verifikasi:

```json
{
  "mmsi": "311001033",
  "name": "WONDER OF THE SEAS",
  "imo": "9838345",
  "callsign": "C6EY2",
  "flag": "Bahamas",
  "length": 362,
  "beam": 64
}
```

Kunci yang tidak dikenal skrip (mis. `note`) **tidak dihapus** saat berkas
ditulis ulang — tiap entri dimulai dari salinan entri lama, jadi catatan
tulisan tangan Anda aman. Yang diperbarui hanya `name`, `imo`, dan `url`.

`name` menentukan URL slug di MyShipTracking. Kapal yang ditambahkan lewat
pencarian menyimpan `url` persis seperti yang diberikan situs — ini penting,
karena situs menulis slug hasil pencarian dengan urutan terbalik
(`…-mmsi-…-imo-…`) daripada yang disusun skrip (`…-imo-…-mmsi-…`). Kalau nama
di situs berbeda dari yang Anda tulis (mis. ada tanda baca), tambahkan `"url"`
berisi URL halaman kapal yang benar — nilai ini menimpa hasil susunan otomatis:

```json
{ "mmsi": "311001033", "name": "WONDER OF THE SEAS",
  "url": "https://www.myshiptracking.com/vessels/wonder-of-the-seas-imo-9838345-mmsi-311001033" }
```

Skrip memverifikasi MMSI benar-benar muncul di halaman sebelum memakai
hasilnya; kalau tidak cocok, sumber itu dianggap gagal.

Field `bbox` di `ships.json` sudah tidak dipakai (sisa versi lama) dan boleh
dihapus. Kapal yang dihapus lewat antarmuka akan hilang dari berkas ini.

---

## Cara kerjanya

Bagian ini menjelaskan **versi lokal**. Di versi Vercel, langkah 1 dan 6 diganti
oleh GitHub Actions dan polling — parser, sumber, dan penggabungan lintasan di
langkah 2–5 tetap sama persis, karena kodenya memang sama.

1. `poll_loop()` mengelilingi daftar kapal, satu permintaan per kapal per
   putaran. Tiap kapal dicoba pada sumber berurutan dan berhenti di sumber
   pertama yang berhasil: **MyShipTracking** lalu **CruiseMapper** (cadangan).
   Setelah tiap putaran, `choose_interval()` memutuskan jeda berikutnya:
   6 detik kalau ada kapal bergerak, 300 detik kalau semua sandar.
   Saat menunggu, `Event` dipakai sebagai pembangun — kapal yang baru
   ditambahkan langsung dicek tanpa menunggu jeda 300 detik itu habis.
2. Parser MyShipTracking membaca empat hal: judul halaman (nama + tipe kapal),
   `canvas_map_generate(...)` (koordinat), prosa "with coordinates ... as
   reported on ..." (waktu pelaporan), dan tabel-tabel HTML. Tiap tabel diuji
   berdasarkan key yang memang dimilikinya — bukan berdasarkan urutannya.
3. Tabel riwayat perjalanan (`Time | Event | Details | Position / Dest | Info`)
   diubah jadi titik-titik lintasan. Kolom dicari lewat nama header, bukan
   posisi kolom, supaya tidak mudah patah.
4. **Baris riwayat terbaru menang** atas blok "posisi saat ini", karena blok
   itu sering tertinggal. Inilah sebabnya posisi yang tampil berbeda dari
   yang tertulis di bagian atas halaman sumber.
5. Lintasan digabung ke `state.json`, jadi jejak tidak hilang saat restart.
6. Browser menerima pembaruan lewat **SSE** (`/api/stream`); penanda kapal
   dianimasikan dengan interpolasi posisi dan haluan.

### Endpoint

Semua endpoint ini ada di **kedua** versi dengan nama yang sama. Yang berbeda
hanya siapa yang mengerjakannya:

| Endpoint | Lokal | Vercel |
|---|---|---|
| `/` | Halaman peta | Halaman peta |
| `/api/state` | Snapshot JSON semua kapal | — (diganti `data.json` di branch `data`) |
| `/api/stream` | SSE, dorong snapshot saat berubah | — (diganti polling 30 detik) |
| `/api/refresh` | Paksa polling **sekarang** | **Minta** GitHub menjalankan poller |
| `/api/search?q=…` | Cari di MyShipTracking; 400 kalau kata kunci < 3 huruf | sama |
| `/api/add?mmsi=…&name=…&imo=…&url=…` | Tambah kapal; 409 kalau MMSI duplikat, bukan angka, atau batas tercapai | sama, tapi menulis lewat commit |
| `/api/remove?mmsi=…` | Hapus kapal; 409 kalau tidak sedang dilacak atau itu kapal terakhir | sama, tapi menulis lewat commit |
| `/api/probe` | — | Diagnosis setelah deploy |

Kode status dan bentuk balasannya sengaja dibuat identik di kedua versi, jadi
UI-nya tidak perlu tahu sedang bicara dengan yang mana.

Di versi lokal, `/api/add` dan `/api/remove` menulis balik `ships.json` supaya
perubahannya bertahan setelah restart. Di versi Vercel, keduanya meng-commit
`ships.json` ke repo — jadi perubahannya justru tercatat di riwayat git.

Satu catatan jujur soal `/api/search`: di versi lokal ia memakai `Fetcher` yang
sama dengan polling, dan throttle menahan lock selama jeda 5 detik. Jadi menekan
**Cari** bisa menunda satu putaran polling hingga ~5 detik. Tidak berbahaya,
tapi nyata. Di versi Vercel tidak ada polling yang berjalan di proses yang sama,
jadi efek itu tidak ada — sebagai gantinya pencarian dibatasi 12 permintaan per
menit per IP, supaya tidak ada yang bisa memakai situs Anda untuk membombardir
MyShipTracking.

### Tombol di antarmuka

| Tombol | Fungsi |
|---|---|
| ➕ Tambah kapal | Buka kotak pencarian / isian manual |
| × (di baris kapal) | Hapus kapal — perlu dua ketukan |
| 🗺️ Peta | Ganti lapisan: terang → satelit → gelap |
| 🔄 Cek | **Polling manual** — paksa pengecekan kapal yang sedang dipilih, tanpa menunggu jadwal |
| 🎯 Ikuti | Peta mengikuti posisi kapal |
| 〰️ Jejak | Tampilkan/sembunyikan garis lintasan |
| ℹ️ Info | Munculkan lagi catatan keterlambatan data setelah disembunyikan |

Daftar **Kapal dilacak** di atas panel adalah pemilihnya: klik satu baris untuk
memindahkan peta, penanda, jejak, dan seluruh panel detail ke kapal itu. Hanya
kapal yang sedang dipilih yang punya penanda di peta — supaya tidak ada
delapan penanda yang saling menutupi.

Pil status di kiri atas menampilkan jumlah kapal dan irama yang sedang dipakai,
mis. `Data MyShipTracking · 2 kapal · 42× cek · tiap 11 s` atau
`… · tiap 5 mnt (sandar)`. Di layar sempit teksnya dipendekkan jadi
`MyShipTracking · 5 mnt` agar muat.

### Tata letak di ponsel (lebar ≤ 720px)

Peta tetap menjadi latar penuh; panel detail berubah menjadi **lembar bawah**
yang bisa dibuka-tutup:

```
┌──────────────────────────────┐
│ ⚓ Lacak Kapal   [status]     │  ← pil status (jam disembunyikan)
│ [Peta][Cek][Ikuti][Jejak]…   │  ← tombol pindah ke atas, rata kiri
│                              │
│           PETA               │
│                              │
│  (catatan keterlambatan)     │  ← muncul di sini kalau data basi
├──────────────────────────────┤
│ Kapal dilacak          2 / 8 │  ← lembar bawah, tertutup
│ WONDER OF THE SEAS        ▸  │
│ Bahamas · Passenger · 362 m  │
└──────────────────────────────┘
```

- Panel mulai **tertutup** supaya peta langsung terlihat. Ketuk kepalanya
  (atau tombol ▸) untuk membuka; tingginya sampai 64% layar.
- Daftar kapal dan kotak tambah kapal ikut jadi lembar penuh di layar sempit;
  kolom pencarian dan tombol **Cari** melipat jadi dua baris penuh.
- Tombol aplikasi pindah ke **atas** supaya tidak pernah tertutup panel, dan
  diratakan ke **kiri** agar lurus dengan nama kapal serta pil status di
  atasnya. Di layar ≤ 360px tombolnya melipat jadi dua baris dengan sendirinya.
- Tombol zoom dan skala bawaan Leaflet **dibuang** di ponsel — layarnya
  sempit dan pinch-to-zoom sudah tersedia. Keduanya dipasang lagi otomatis
  kalau jendela diperbesar atau ponsel diputar ke mode lanskap.
- Atribusi peta (syarat lisensi OpenStreetMap/Esri) diangkat ke atas lembar
  bawah supaya tetap terbaca.
- Sasaran sentuh diperbesar: tombol lebih tinggi, baris data lebih lega.

Perubahan ukuran jendela ditangani otomatis (diredam 250 ms) — tidak perlu
muat ulang halaman setelah memutar ponsel.

Tombol **🔄 Cek** melaporkan hasilnya, bukan sekadar "sudah dicek":
`Laporan baru: 2026-09-13 14:20` kalau memang ada posisi baru, atau
`Sudah dicek — kapal belum kirim laporan baru (masih 2026-09-13 10:43)`
kalau tidak ada. Ini penting supaya Anda tidak salah menyimpulkan bahwa
script-nya macet padahal kapalnya yang belum melapor.

Spanduk kuning di bawah memuat catatan keterlambatan data dan muncul otomatis
saat umur data melewati `STALE_SEC` (6 jam). Tombol **×** di sudut kanannya
menyembunyikannya, dan ia **tetap tersembunyi** walau data diperbarui —
tekan **ℹ️ Info** untuk memunculkannya kembali.

### Catatan zona waktu

Stempel waktu di halaman sumber **tidak mencantumkan zona waktu**. Skrip
menganggapnya UTC, yang cukup untuk mengurutkan titik lintasan, tetapi nilai
jam absolutnya bisa meleset beberapa jam. Jangan pakai kolom waktu untuk
menyimpulkan sesuatu yang presisi.

### Catatan istilah

- `reported_human` (mis. `"1 d ago"`) adalah **teks mentah dari situs**,
  bukan hitungan skrip. Bisa berbeda dari `reported` (`2026-09-13 10:43`) —
  percayai `age`, yang dihitung dari selisih waktu server.
- `nav_status` juga teks dari situs dan sering tidak diperbarui; kapal
  bersandar bisa masih tertulis "Under way using engine".

---

## Menjalankan di Vercel

Bagian ini menjelaskan versi publiknya. Kalau Anda hanya memakai versi lokal,
lewati saja — tidak ada yang berubah di sana.

### Apa yang hilang, sebelum Anda memutuskan

Vercel Hobby tidak bisa menjalankan aplikasi ini apa adanya. Batasnya bukan
soal selera, ini angka dari dokumentasi mereka:

| | Vercel Hobby | Yang dibutuhkan aplikasi ini |
|---|---|---|
| Interval cron | **minimum 1× sehari** | 6 detik |
| Proses latar belakang | tidak ada | `poll_loop` sebagai thread abadi |
| Berkas yang bertahan | tidak ada | `state.json` |
| Durasi maksimum fungsi | 300 detik | SSE terbuka tanpa batas |

Selisihnya lima orde besaran, jadi ini **bukan** cara membuat versi lokal
berjalan di Vercel — ini arsitektur yang berbeda, dengan kemunduran nyata:

| | Lokal | Vercel |
|---|---|---|
| Pembaruan posisi | **6 detik** (300 saat sandar) | **10 menit–5 jam**, tergantung kapan halaman dibuka |
| Cara browser menerima data | SSE, didorong saat berubah | polling tiap 30 detik |
| Tombol 🔄 Cek | mengecek saat itu juga (~3 detik) | **meminta**, lalu menunggu ~20–60 detik |
| Jejak lintasan | tersimpan di komputer Anda | tersimpan di repo, publik |
| Tambah/hapus kapal | tulis ke `ships.json` lokal | commit ke repo, perlu token |
| Butuh akun | tidak | GitHub + Vercel |

Hiburannya: data dari situs sumbernya sendiri tertinggal berjam-jam (pernah
terukur `age` ≈ 7 jam). Selisih 6 detik dan 5 menit praktis tidak terasa di
atas data yang umurnya beberapa jam. Yang benar-benar terasa adalah tombol
🔄 Cek, yang tidak lagi instan.

### Arsitekturnya

```
GitHub Actions                     Vercel
┌──────────────────────┐          ┌────────────────────────┐
│ poll.yml             │          │ index.html (statis)    │
│  jadwal + diminta    │          │                        │
│  tracker.py --once   │          │ api/search  api/add    │
│         ↓            │          │ api/search  api/add    │
│  branch `data`  ─────┼── dibaca │ api/remove  api/refresh│
│  data.json           │    oleh  │        ↓               │
│  state.json          │          │  GitHub Contents API   │
└──────────────────────┘          │  → commit ships.json   │
                                  └────────────────────────┘
```

Yang penting: **`data.json` bentuknya sama persis dengan `/api/state`**. Itu
disengaja, supaya `apply()` di `index.html` tidak perlu tahu bedanya.

Branch `data` selalu berisi **tepat satu commit** (di-force-push tiap kali),
jadi repo tidak membengkak oleh ratusan commit sehari. Branch itu hanya berisi
data, tidak ada kode.

### Langkah-langkahnya

**1. Repo.** Kode ini sudah ada di
`https://github.com/Riyantttsjddj/ais-cruise-monitor`. Kalau Anda memakai repo
Anda sendiri, **ubah satu baris di `index.html`** — cari `URL_JAUH` di dekat
komentar "sumber data" dan ganti pemilik/nama reponya. Kalau tidak diubah,
halaman yang di-deploy akan membaca data dari repo ini, bukan repo Anda.

**2. Token GitHub.** Buat *fine-grained token* di
**Settings → Developer settings → Personal access tokens → Fine-grained**,
dengan akses hanya ke repo ini dan dua izin:

| Izin | Untuk apa |
|---|---|
| **Contents: Read and write** | meng-commit `ships.json` saat kapal ditambah/dihapus |
| **Actions: Read and write** | memicu `poll.yml` saat tombol 🔄 Cek ditekan |

Token ini **hanya dipakai di sisi server** (fungsi Vercel). Ia tidak pernah
sampai ke browser.

**3. Deploy.** Impor repo itu di Vercel. **Setel "Framework Preset" ke
`Other`.** Ini bukan langkah kosmetik — kalau dibiarkan `Flask`, deploy-nya
**gagal** dengan pesan yang menyesatkan:

```
Error: No Flask entrypoint found in default locations, but found potential
entrypoints: api/add.py (variable: handler) ...
```

Pesan itu menyalahkan berkas Anda, padahal berkasnya sudah benar. Yang salah
adalah preset: **preset framework mengalahkan fungsi berbasis berkas**, jadi
Vercel berhenti mencari `api/*.py` dan mulai mencari entrypoint Flask yang
memang tidak ada. Setel ke `Other`, dan tiap `.py` di `api/` kembali menjadi
fungsi tersendiri.

Kalau proyeknya sudah telanjur dibuat dengan preset Flask, ubah di
**Settings → Build and Development Settings → Framework Preset → Other**, lalu
deploy ulang. Tidak perlu mengubah kode sama sekali.

Tidak ada build step dan tidak ada dependensi — seluruh aplikasi hanya memakai
pustaka standar Python.

**4. Environment Variables** (Settings → Environment Variables):

| Nama | Isi |
|---|---|
| `GH_TOKEN` | token dari langkah 2 |
| `GH_REPO` | `Riyantttsjddj/ais-cruise-monitor` (pemilik/nama) |

Tanpa keduanya, halaman tetap tampil dan petanya tetap jalan — yang mati hanya
tambah/hapus/🔄 Cek, dengan pesan yang menjelaskan penyebabnya.

**5. Periksa.** Buka `https://<nama-anda>.vercel.app/api/probe`. Balasannya
JSON dan memeriksa tiga hal yang tidak bisa dipastikan dari sini:

```json
{
  "modul":  { "ok": true, "berkas": "tracker.py" },
  "github": { "ok": true, "boleh_menulis": true },
  "sumber": { "ok": true, "status": 200 },
  "ringkas": "semua siap"
}
```

| Bagian | Kalau `ok: false` |
|---|---|
| `modul` | `tracker.py` tidak ikut ter-bundle — laporkan, ini bug |
| `github` | tokennya salah, kedaluwarsa, atau kurang izin |
| `sumber` | Cloudflare menolak IP Vercel. Pencarian dari UI tidak akan jalan; peta tetap jalan karena datanya dari branch `data`, bukan dari Vercel |

Balasan itu tidak pernah memuat isi token — hanya ada/tidak, panjangnya, dan
awalan empat hurufnya.

**6. Nyalakan poller-nya.** Buka tab **Actions** di repo, pilih workflow
**poll**, tekan **Run workflow** sekali. Setelah itu ia jalan sendiri tiap 5
menit.

### Kenapa poller-nya di GitHub, bukan di Vercel

Cron Vercel Hobby minimum **sekali sehari**, dan ekspresi yang lebih rapat
**gagal saat deploy** — bukan diam-diam melambat, tapi ditolak. GitHub Actions
memberi interval terpendek **5 menit** dan gratis tanpa batas untuk repo publik.

Dua hal soal Actions yang perlu diketahui:

- Jadwalnya **bisa tertunda saat beban tinggi**, terutama di awal jam. Karena
  itu cronnya ditulis `2-57/5 * * * *`, bukan `*/5` — pola pertama tidak pernah
  jatuh tepat di menit :00.
- **Yang pertama itu bukan sekadar teori, dan akibatnya jauh lebih besar daripada
  "tertunda".** Terukur pada repo ini: dalam 11 jam pertama sejak workflow
  dibuat, jadwal yang meminta 288 run/hari itu hanya menghasilkan **4 run** —
  pukul 21:45, 23:36, 01:36, dan 07:02 UTC. Jarak antar-run 1 jam 50 menit, 2
  jam, lalu 5 jam 26 menit. Ekspresi cronnya **bukan** penyebabnya: sudah diuji
  banding dengan ekspresi kedua, dan saat itu dua-duanya sama-sama nol lalu
  dua-duanya mulai jalan sendiri setelah beberapa jam. Jadi jangan buang waktu
  meneliti YAML-nya.
- Karena itu kesegaran data **tidak boleh bersandar pada jadwal itu saja.**
  Halaman menyegarkan diri: kalau snapshot terakhir sudah lebih tua dari 10
  menit saat halaman dibuka, ia meminta satu run sendiri (jalur yang sama dengan
  tombol 🔄 — terbukti selesai dalam ~20 detik), dengan jeda 5 menit per peramban
  supaya tidak membanjiri GitHub. Selama halamannya sesekali dibuka, datanya ikut
  segar.
- Workflow terjadwal di repo publik **dimatikan otomatis setelah 60 hari tanpa
  aktivitas repo**. Karena `poll` mendorong ke branch `data` tiap kali jalan, itu
  mestinya sudah cukup — tapi tidak jelas apakah dorongan dari `GITHUB_TOKEN`
  dihitung sebagai "aktivitas". Karena itu ada `heartbeat.yml`: satu commit
  kosong tiap Senin sebagai asuransi. Kalau ternyata tidak perlu, ia hanya
  menambah 52 commit kosong setahun.

Singkatnya: **jangan andalkan jadwal GitHub untuk ketepatan waktu.** Yang bisa
diandalkan adalah tombol 🔄 dan penyegaran-saat-dibuka; jadwalnya sendiri
berfungsi sebagai jaring pengaman, bukan sebagai metronom.

### Kalau disalahgunakan

Anda memilih **tanpa kunci**, jadi siapa pun yang menemukan alamatnya bisa
menambah atau menghapus kapal. Itu keputusan yang sadar, dan yang menahan
kerusakannya:

| Penjaga | Yang dilakukan |
|---|---|
| **Batas 8 kapal** | penambahan ke-9 ditolak. Ini penjaga yang sebenarnya — tidak ada yang bisa membanjiri repo dengan ribuan kapal |
| **Tanpa header CORS** | situs lain tidak bisa memanggil endpoint tulis dari browser pengunjungnya |
| **Pembatas laju per-IP** | menahan tombol yang ditekan bertubi-tubi. **Best-effort saja** — disimpan di memori proses, dan Vercel bisa menjalankan beberapa instans lalu mematikannya kapan saja |
| **Riwayat git** | tiap perubahan tercatat dan bisa dibalik |

Risikonya polusi repo, bukan pencurian data: token Anda tidak pernah sampai ke
browser, hanya dipakai di sisi server. Kalau nanti benar-benar disalahgunakan,
urutannya: balikkan commitnya (`git revert`), lalu putar ulang tokennya di
GitHub, lalu hapus deployment-nya di Vercel. Tidak ada kunci `ADMIN_KEY` di
kode ini — kalau Anda ingin satu, itu perubahan yang perlu ditulis dulu.

### Kalau parser rusak (versi Vercel)

Log-nya ada di tab **Actions** → pilih run → langkah "Satu putaran polling".
Kalau semua sumber gagal, langkah itu keluar dengan kode 1 dan run-nya ditandai
gagal — jadi parser yang rusak terlihat, bukan lewat diam-diam berhari-hari.

Satu penjaga khusus: jumlah titik jejak dicatat **sebelum** dan **sesudah**
tiap run. `Store._merge_trail()` tidak pernah menghapus titik, jadi jejak yang
**menyusut** selalu berarti `state.json` gagal dimuat dan run itu mulai dari
nol. Kejadian itu pernah lolos karena workflow-nya tetap melaporkan sukses;
sekarang ia menggagalkan run-nya dengan menyebut MMSI yang bermasalah.

---

## Kalau parser rusak (versi lokal)

Gejalanya: panel status menampilkan error, atau field tertentu kosong.
Cara mendiagnosis:

```bash
# Lihat log kegagalan
tail -f run.log

# Periksa struktur HTML terkini
python3 -c "
import sys; sys.path.insert(0,'/home/ais-cruise-monitor')
from tracker import Fetcher
raw,_ = Fetcher().get('https://www.myshiptracking.com/vessels/wonder-of-the-seas-imo-9838345-mmsi-311001033')
open('/tmp/page.html','w').write(raw); print(len(raw),'bytes -> /tmp/page.html')
"
```

Lalu cari pola yang berubah di `/tmp/page.html`. Titik yang paling sering
berubah: nama kelas CSS di `canvas_map_generate`, dan urutan kolom tabel
riwayat.

---

## Berkas

| Berkas | Isi |
|---|---|
| `tracker.py` | Server + scraper (Python standar, tanpa dependensi) |
| `index.html` | Antarmuka peta (Leaflet 1.9.4 dari CDN unpkg) |
| `ships.json` | Daftar kapal yang dilacak |
| `state.json` | Jejak lintasan tersimpan (dibuat otomatis) |
| `run.log` | Log runtime (dibuat otomatis) |
| `vlib.py` | Utilitas bersama fungsi Vercel: GitHub API, bentuk balasan, pembatas laju |
| `api/*.py` | Satu berkas per endpoint Vercel |
| `vercel.json` | Konfigurasi Vercel: durasi maksimum tiap fungsi |
| `.github/workflows/poll.yml` | Pengganti `poll_loop` di Vercel — diminta tiap 5 menit, tapi GitHub memberikannya jauh lebih jarang |
| `.github/workflows/heartbeat.yml` | Commit kosong mingguan, asuransi agar workflow terjadwal tidak dimatikan |
| `.github/workflows/probe.yml` | Diagnosis sekali jalan: apakah Cloudflare menerima runner GitHub |

`state.json`, `data.json`, dan `run.log` ada di `.gitignore` — di branch `main`.
Di branch `data` dua yang pertama justru memang disimpan; itulah gunanya.
