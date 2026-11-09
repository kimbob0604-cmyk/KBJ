/// <reference types="vite/client" />

// 빌드 시점 상수(vite.config.ts `define`). 공개 빌드 = 'public', 로그인 빌드 = 'login',
// 공개 사이트(맥이 올린 정적 스냅샷 — 사용자 결정 2026-11-09) = 'site'.
// 이 값은 main.ts 한 곳에서만 읽는다 — 나머지 코드는 tier 를 인자로 받는다(시험에서 두 등급을 다 돌리려고).
interface ImportMetaEnv {
  readonly VITE_KBJ_TIER: 'public' | 'login' | 'site';
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
