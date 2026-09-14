// Gerbang kode akses di edge — ADAPTOR TIPIS.
//
// Seluruh keputusan ada di `gerbang.js`; berkas ini hanya menyambungkannya ke
// Request/Response dan sengaja dibuat setipis mungkin, supaya tidak ada logika
// yang hanya bisa diuji dengan cara mendeploy.
//
// Proyek ini tidak memakai framework, jadi dua hal berbeda dari contoh Next.js:
// `request` di sini adalah `Request` web standar (bukan NextRequest), dan
// `next()` datang dari paket `@vercel/functions`, bukan dari `next/server`.
// Itu juga sebabnya `package.json` ada — Vercel mensyaratkan `"type": "module"`
// untuk middleware di proyek tanpa framework.
import { next } from "@vercel/functions";

import { IZIN, NAMA_COOKIE, TOLAK, bacaCookie, putuskan } from "./gerbang.js";

export const config = {
  // Semua jalur KECUALI halaman kunci, endpoint pembuka, dan favicon. Bentuk
  // negative lookahead ini persis contoh resmi di dokumentasi Vercel.
  matcher: ["/((?!kunci\\.html|api/buka|favicon\\.ico).*)"],
};

export default function middleware(request) {
  const jalur = new URL(request.url).pathname;
  const cookie = bacaCookie(request.headers.get("cookie") || "", NAMA_COOKIE);
  const aksi = putuskan(jalur, cookie, process.env.NILAI_KUNCI);

  if (aksi === IZIN) return next();

  if (aksi === TOLAK) {
    // API dapat JSON, bukan halaman kunci. JS di halaman mem-parse balasannya;
    // kalau yang datang HTML, gagalnya muncul sebagai galat parse yang
    // menyesatkan dan menutupi sebab sebenarnya.
    return Response.json(
      { ok: false, error: "terkunci — masukkan kode akses dulu" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }

  // 302, BUKAN 301/308. Keduanya di-cache peramban selamanya: peramban yang
  // pernah membuka situs saat terkunci akan terus dilempar ke halaman kunci
  // walau cookie-nya sudah sah, dan pengguna terjebak tanpa jalan keluar
  // selain membersihkan cache. Jangan diubah tanpa alasan yang kuat.
  return Response.redirect(new URL("/kunci.html", request.url), 302);
}
