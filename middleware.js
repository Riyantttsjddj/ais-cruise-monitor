// Gerbang kode akses di edge Vercel — adaptor tipis.
//
// Seluruh keputusannya ada di `gerbang.js`, yang tidak mengimpor apa pun dan
// karena itu bisa diuji tanpa Vercel (`/tmp/uji_kunci.js`). Berkas ini cuma
// menerjemahkan keputusan itu jadi `Response`.
//
// Proyek ini tidak memakai framework, jadi `next()` datang dari paket
// `@vercel/functions`, BUKAN dari `next/server`. Itu juga alasan `package.json`
// ada dan berisi `"type": "module"`.

import { next } from "@vercel/functions";
import {
  IZIN,
  IZIN_SEKALI,
  KUNCI,
  NAMA_COOKIE,
  NAMA_SEKALI,
  TOLAK,
  putuskan,
} from "./gerbang.js";

export const config = {
  // Negative lookahead: jalur-jalur ini TIDAK pernah lewat gerbang. Tanpa
  // pengecualian `kunci.html`, halaman kuncinya mengunci dirinya sendiri; tanpa
  // `api/buka`, tidak ada cara memasukkan kodenya sama sekali.
  matcher: ["/((?!kunci\\.html|api/buka|favicon\\.ico).*)"],
};

const HALAMAN_KUNCI = "/kunci.html";

// Menghapus cookie: nilai kosong + Max-Age=0. Nama, Path, dan atribut lain harus
// sama persis dengan saat dipasang — kalau beda, peramban menganggapnya cookie
// lain dan yang lama tetap tertinggal.
function hapus(nama) {
  return `${nama}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0`;
}

export default function middleware(request) {
  const jalur = new URL(request.url).pathname;
  const aksi = putuskan(
    jalur,
    request.headers.get("cookie") || "",
    process.env.NILAI_KUNCI,
  );

  if (aksi === IZIN) return next();

  if (aksi === IZIN_SEKALI) {
    // Sajikan halamannya SEKALIGUS habiskan tiketnya, dalam satu permintaan.
    // Inilah satu-satunya tempat tiket dibuang, dan itu yang membuat muat ulang
    // berikutnya jatuh ke KUNCI lagi. `next()` — bukan `Response` buatan sendiri
    // — karena hanya ini yang tahu cara melanjutkan ke berkas statisnya; header
    // di sini ditambahkan ke balasan aslinya, bukan menggantikannya.
    return next({ headers: { "Set-Cookie": hapus(NAMA_SEKALI) } });
  }

  if (aksi === TOLAK) {
    // JSON, bukan halaman kunci: pemanggilnya JavaScript, dan halaman HTML tidak
    // bisa di-parse — gejalanya jadi "JSON parse error", bukan "terkunci".
    return Response.json(
      { ok: false, error: "terkunci — masukkan kode akses dulu" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }

  // KUNCI. Cookie-nya ikut dihapus, bukan cuma dialihkan: dengan begitu tidak
  // ada sisa sesi yang tertinggal di perangkat begitu halamannya ditinggalkan.
  //
  // 302, dan itu bukan kelalaian: 301/308 di-cache peramban SELAMANYA, sehingga
  // peramban yang pernah membuka situs saat terkunci akan terus dilempar ke
  // halaman kunci walau baru saja memasukkan kode yang benar.
  const kepala = new Headers({
    Location: new URL(HALAMAN_KUNCI, request.url).toString(),
    "Cache-Control": "no-store",
  });
  kepala.append("Set-Cookie", hapus(NAMA_COOKIE));
  kepala.append("Set-Cookie", hapus(NAMA_SEKALI));
  return new Response(null, { status: 302, headers: kepala });
}
