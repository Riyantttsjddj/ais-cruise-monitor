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
//
// Dua tingkat, dan bedanya disengaja:
//   * ENDPOINT (`/api/*`) cukup bermodal cookie — halaman yang sudah terbuka
//     memanggilnya terus-menerus, dan kalau tiap panggilan menuntut kode, tidak
//     ada yang bisa dipakai.
//   * HALAMAN menuntut tiket sekali-pakai di atas cookie, supaya tiap muat ulang
//     menanyakan kodenya lagi.

export const IZIN = "izin";
// Izinkan, TAPI pakai tiket sekali-pakai yang dibawa permintaan ini. Middleware
// menerjemahkannya jadi `next()` yang menghapus cookie tiket dari peramban.
export const IZIN_SEKALI = "izin-sekali";
export const KUNCI = "kunci";
export const TOLAK = "tolak";

export const NAMA_COOKIE = "kunci";

// Cookie penanda "baru saja memasukkan kode". Lihat `putuskan` untuk alasannya.
export const NAMA_SEKALI = "sekali";

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


export function putuskan(jalur, headerCookie, nilaiKunci) {
  const path = String(jalur || "/");
  if (JALUR_BEBAS.includes(path)) return IZIN;

  // Yang masuk header mentah, bukan nilai per cookie. Itu disengaja: dengan
  // begitu tidak ada cara middleware lupa membaca cookie kedua — kesalahan yang
  // akibatnya "halaman terbuka tanpa kode" dan tidak kelihatan dari call site.
  const dapat = bacaCookie(headerCookie, NAMA_COOKIE);
  const sekali = bacaCookie(headerCookie, NAMA_SEKALI);

  const diharapkan = String(nilaiKunci ?? "");
  const api = path === "/api" || path.startsWith("/api/");

  // Gagal tertutup. Urutannya PENTING: pemeriksaan ini harus SEBELUM
  // perbandingan. Kalau nilai yang diharapkan kosong (env belum diatur) dan
  // cookie-nya juga kosong, perbandingan biasa akan menganggap keduanya cocok
  // dan seluruh situs terbuka bagi siapa pun yang tidak mengirim cookie.
  if (!diharapkan) return api ? TOLAK : KUNCI;

  if (!samaAman(dapat, diharapkan)) return api ? TOLAK : KUNCI;

  // Endpoint cukup bermodal cookie. Halamannya yang butuh lebih.
  if (api) return IZIN;

  // HALAMAN butuh tiket sekali-pakai, bukan cuma cookie.
  //
  // Ini yang membuat tiap muat ulang menuntut kode lagi. Cookie saja tidak
  // cukup: `sekali` hanya dipasang `/api/buka` (jadi harus mengetik kode dulu),
  // dan dihapus dari peramban begitu satu halaman tersaji. Muat ulang membawa
  // cookie `kunci` tanpa `sekali` — jadi jatuh ke KUNCI lagi.
  //
  // Efek sampingnya disengaja: cookie yang cuma didapat dari memindahkan profil
  // peramban TIDAK bisa membuka halaman; pemiliknya masih harus mengetik kodenya.
  if (sekali) return IZIN_SEKALI;
  return KUNCI;
}
