/// <reference types="vitest/config" />
// KBJ 웹 빌드 설정 — docs/p3_design.md §6.
//
// 빌드 두 벌(D-P3-2): `--mode public` → dist-public(정적 Pages), `--mode login` → dist-login(VM API 와 같은 출처).
// 등급은 빌드 시점 상수 `import.meta.env.VITE_KBJ_TIER` 하나로 가른다. main.ts 의 분기가 상수로 접혀
// 공개 번들에는 로그인 코드(`data/api.ts`·`data/auth.ts`)가 들어가지 않는다 — scripts/check-bundle.mjs 가 확인한다.
import { defineConfig, type Plugin } from 'vite';

type Tier = 'public' | 'login';

// 로그인 CSP 는 API 서버 헤더가 정본이다(§5.4). 메타 태그는 같은 정책의 두 번째 겹(frame-ancestors 는 메타에서 무시돼 뺀다).
const CSP_LOGIN = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  "worker-src 'self'",
  "manifest-src 'self'",
  "base-uri 'none'",
  "form-action 'self'",
].join('; ');

// 공개판은 Pages 라 헤더를 못 붙인다 — 메타 CSP 가 유일한 정책. TradingView 위젯(외부 스크립트·iframe)만 더 연다(§6.6).
const CSP_PUBLIC = [
  "default-src 'self'",
  "script-src 'self' https://s3.tradingview.com",
  "style-src 'self'",
  "img-src 'self' data:",
  "connect-src 'self'",
  'frame-src https://s.tradingview.com https://www.tradingview-widget.com https://s3.tradingview.com',
  "worker-src 'self'",
  "manifest-src 'self'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function tierOf(mode: string): Tier {
  // 모르는 모드는 공개로 — 애매하면 덜 여는 쪽(DATA_TIERS 원칙의 반대 방향이 아니라, 로그인 코드를 넣지 않는 쪽)
  return mode === 'login' ? 'login' : 'public';
}

function cspMeta(tier: Tier): Plugin {
  return {
    name: 'kbj-csp-meta',
    apply: 'build',
    transformIndexHtml() {
      return [
        {
          tag: 'meta',
          attrs: { 'http-equiv': 'Content-Security-Policy', content: tier === 'login' ? CSP_LOGIN : CSP_PUBLIC },
          injectTo: 'head-prepend',
        },
      ];
    },
  };
}

export default defineConfig(({ mode }) => {
  const tier = tierOf(mode);
  return {
    // Pages 는 /<레포>/ 아래, 로그인은 / 에서 열린다 — 상대 경로로 둘 다 맞춘다
    base: './',
    define: {
      'import.meta.env.VITE_KBJ_TIER': JSON.stringify(tier),
    },
    plugins: [cspMeta(tier)],
    build: {
      outDir: tier === 'login' ? 'dist-login' : 'dist-public',
      emptyOutDir: true,
      target: 'es2022',
      sourcemap: false,
      modulePreload: { polyfill: false },
      rollupOptions: {
        input: { main: 'index.html', sw: 'src/pwa/sw.ts' },
        output: {
          // 서비스 워커는 범위가 앱 루트여야 해서 고정 이름으로 루트에 둔다
          entryFileNames: (chunk) => (chunk.name === 'sw' ? 'sw.js' : 'assets/[name]-[hash].js'),
        },
      },
    },
    server: {
      host: '127.0.0.1',
      // 로그인 개발 모드: 로컬 API(python -m kbj.services.api serve)로 넘긴다. 이 설정은 번들에 들어가지 않는다
      proxy: tier === 'login' ? { '/api': 'http://127.0.0.1:8000' } : undefined,
    },
    test: {
      environment: 'jsdom',
      include: ['test/**/*.test.ts'],
      setupFiles: ['test/setup.ts'],
      // tokens.test.ts 가 tokens.css?raw 를 읽는다 — vitest 는 기본으로 CSS 를 빈 문자열로 바꾼다
      css: { include: [/\/src\/design\/.*\.css/] },
      restoreMocks: true,
      unstubGlobals: true,
    },
  };
});
