// Gerbang kode akses — LOGIKA KEPUTUSAN SAJA, tanpa impor apa pun.
//
// Berkas ini sengaja dipisah dari `middleware.js`. Middleware mengimpor
// `@vercel/functions`, yang tidak bisa dijalankan Node biasa; berkas ini tidak
// mengimpor apa pun, jadi seluruh keputusan di sini bisa diuji langsung
// (`/tmp/uji_kunci.js`). Kalau logikanya ditulis menempel di middleware,
// satu-satunya cara mengujinya adalah mendeploy — dan salah di sini artinya
// semua orang terkunci dari situsnya sendiri.
//
// Catatan cakupan: karena repo-nya tetap publik, gerbang ini menjaga HALAMAN
// dan ENDPOINT-nya, bukan datanya. `data.json` di branch `data` masih bisa
// dibaca siapa pun yang tahu URL raw-nya.

export const IZIN = "izin";
export const KUNCI = "kunci";
export const TOLAK = "tolak";

export const NAMA_COOKIE = "kunci";

// Jalur yang TIDAK pernah dialihkan. Tanpa pengecualian ini halaman kuncinya
// mengunci dirinya sendiri, dan situsnya tidak bisa dibuka sama sekali.
export const JALUR_BEBAS = ["/kunci.html", "/api/buka", "/favicon.ico"];


export function bacaCookie(header, nama) {
  // Nilainya sengaja TIDAK di-decode: tokennya heksadesimal, jadi tidak ada
  // yang perlu di-escape — dan `decodeURIComponent` melempar kalau ada "%" yang
  // tidak valid, artinya satu cookie rusak bisa menjatuhkan seluruh halaman.
  for (const bagian of String(header || "").split(";")) {
    const i = bagian.indexOf("=");
    if (i < 0) continue;
    // Dibandingkan sebagai nama UTUH, bukan awalan: kalau memakai
    // `startsWith`, cookie bernama `kuncix` ikut dianggap milik kita.
    if (bagian.slice(0, i).trim() === nama) return bagian.slice(i + 1).trim();
  }
  return "";
}


export function samaAman(a, b) {
  const x = String(a ?? "");
  const y = String(b ?? "");
  // Panjang berbeda langsung dinilai tidak sama. Ini membocorkan panjang nilai
  // yang diharapkan — tapi panjangnya tetap (token heksadesimal) dan bukan
  // rahasia; yang dijaga isinya.
  if (x.length !== y.length) return false;
  // Sengaja TIDAK keluar lebih awal saat sudah ketemu beda: keluarnya lebih
  // awal membuat waktu balasan bergantung pada berapa karakter awal yang benar,
  // dan itu cukup untuk menebak token karakter demi karakter.
  let beda = 0;
  for (let i = 0; i < x.length; i++) beda |= x.charCodeAt(i) ^ y.charCodeAt(i);
  return beda === 0;
}


export function putuskan(jalur, cookieKunci, nilaiKunci) {
  const path = String(jalur || "/");
  if (JALUR_BEBAS.includes(path)) return IZIN;

  const diharapkan = String(nilaiKunci ?? "");
  const dapat = String(cookieKunci ?? "");
  const api = path === "/api" || path.startsWith("/api/");

  // Gagal tertutup. Urutannya PENTING: pemeriksaan ini harus SEBELUM
  // perbandingan. Kalau nilai yang diharapkan kosong (env belum diatur) dan
  // cookie-nya juga kosong, perbandingan biasa akan menganggap keduanya cocok
  // dan seluruh situs terbuka bagi siapa pun yang tidak mengirim cookie.
  if (!diharapkan) return api ? TOLAK : KUNCI;

  if (samaAman(dapat, diharapkan)) return IZIN;
  return api ? TOLAK : KUNCI;
}
