# web/ — KBJ 국장 터미널 SPA

설계: `docs/p3_design.md` §6(프런트)·§6.6(공개 대 로그인 빌드)·§7.3(Pages). 화면 구성: `docs/design/preview_synthetic.html`(사용자 승인 합성 미리보기). MD6 의 CSS·JS·로고·이미지는 쓰지 않는다 — 토큰과 배치는 KBJ 미리보기에서 직접 다시 만든 것이다.

- Vite 6 + TypeScript 5(strict) + 바닐라 DOM. **런타임 의존성 0**, dev 의존성 9개(`package.json` — 버전 고정).
- 같은 SPA 를 **두 벌** 빌드한다. 등급은 빌드 상수 `import.meta.env.VITE_KBJ_TIER` 하나이고 `src/main.ts` 한 곳에서만 읽는다.

| 명령 | 내용 |
|---|---|
| `npm ci --ignore-scripts` | 설치(lock 그대로, 설치 스크립트 실행 안 함) |
| `npm run lint` · `npm run typecheck` · `npm test` | eslint(타입 정보 규칙 + 구조 규칙) · tsc · vitest(jsdom) |
| `npm run build:public` | 공개 빌드 → `dist-public/` (정적 Pages, `./data/*.json` 만 읽음) |
| `npm run build:login` | 로그인 빌드 → `dist-login/` (VM API 와 같은 출처 `/api`) |
| `node scripts/check-bundle.mjs` | 두 빌드 검사: 공개 번들에 API 경로·CSRF 헤더·`KBJ_`·세션 쿠키 이름 0, 로그인 번들에 TradingView 주소 0, gzip 예산 |
| `node scripts/merge-public-data.mjs <public-data> [dist-public]` | `manifest.json` 에 적힌 공개 JSON 만 `dist-public/data/` 로(로그인 등급 출처 이름이 source 에 있으면 실패) |
| `npm run gen:types [-- --check]` | `src/api/openapi.json`(A 묶음) → `src/api/types.gen.ts`(W2 묶음). `--check` 는 낡았으면 실패 |
| `npm run dev` · `npm run dev:login` | 개발 서버(127.0.0.1). 로그인 모드는 `/api` 를 `127.0.0.1:8000` 으로 넘긴다 |

완료 확인(§9.4 W): `cd web && npm ci --ignore-scripts && npm run lint && npm run typecheck && npm test && npm run build:public && npm run build:login && node scripts/check-bundle.mjs`

## 구조

```
src/
  main.ts            등급 상수 분기 → boot/public 또는 boot/login (다른 쪽 청크는 번들에 생기지 않는다)
  boot/public.ts     정적 데이터 소스 + TradingView → mountApp
  boot/login.ts      세션 확인 → 로그인 화면 → mountApp, 401 이면 앱을 내리고 로그인 화면
  app/app.ts         셸 조립(상단 바·띠·탭/미니맵·페이지·바닥 줄), 페이지는 처음 보일 때 붙인다
  app/shell.ts       상단 바(브랜드·KST 시계·등급 배지·스킨·로그아웃)
  app/pager.ts       탭·미니맵·scroll-snap 스와이프·←/→·해시 #/p/<n>
  app/pages.ts       13페이지 등록부(번호·제목·단계·등급·자물쇠 사유) + 위젯 모듈 찾기(glob)
  app/skin.ts        스킨 5종(<html data-skin>, localStorage 'kbj-skin' — try/catch)
  app/refresh.ts     주기 실행(탭이 숨으면 멈춤)
  app/types.ts       셸 ↔ 페이지 모듈 약속(AppContext·PageContext·PageModule·RibbonModule)
  data/source.ts     DataSource·Envelope·오류형(AuthRequiredError·LockedError·DataError)
  data/static.ts     공개: ./data/<이름>.json (슬래시 든 키는 요청 없이 LockedError)
  data/api.ts        로그인: 같은 출처 API, 쿠키 세션, 401 → 로그인 화면        ← boot/login 만 import
  data/auth.ts       로그인 폼·me·로그아웃(CSRF 헤더, 메모리 보관)               ← boot/login 만 import
  ui/                dom(h)·fmt(억·조·KST)·quality·panel·lock·table·seg·svg·chip
  pages/placeholder.ts  빈 자리 페이지(준비 중(Pn)·등급 자물쇠)
  pages/tradingview.ts  TradingView 임베드(공개 전용)                            ← boot/public 만 import
  pwa/               sw.ts(앱 셸만 캐시 — 허용 목록 routes.ts)·register.ts
  design/            tokens.css(스킨 5종)·base.css·components.css
test/                vitest — fmt·quality·tier·pager·a11y(axe-core)·tokens(대비)·source·auth·ui·shell·sw·scripts
```

## W2 에게 — 페이지·띠를 붙이는 약속

W(셸)와 W2(위젯)는 같은 파일을 고치지 않는다. 셸은 **파일이 있으면** 찾아 붙이고, 없으면 '준비 중' 자리를 그린다.

- 페이지 1·2·4·10: `src/pages/p1_market.ts`·`p2_board.ts`·`p4_flows.ts`·`p10_etf.ts` 가 `export default` 로 `PageModule` 을 내보낸다(`app/pages.ts` 의 `import.meta.glob('../pages/p{1,2,4,10}_*.ts')`).
  `mount(ctx: PageContext)` 는 페이지가 **처음 보일 때 한 번** 불린다. 정리 함수를 돌려주면 앱이 내려갈 때 부른다.
- 상단 띠: `src/app/ribbon.ts` 가 `export default` 로 `RibbonModule`(`mount(el, ctx)`). 칩은 `ui/chip.ts` 의 `chip(ctx.tier, {...})`.
- 등급: 패널은 `panel(ctx.tier, { tier: 'login', lock: 'krx', ... })` 로 만든다. 공개 빌드면 자물쇠만 그리고 `p.locked === true` — **그때는 데이터를 요청하지 않는다**. 공개 데이터 키는 파일 이름(`'calendar'`), 로그인 키는 API 경로(`'market/summary'`). 공개 빌드에서 로그인 키를 물으면 요청 없이 `LockedError`.
- 시세 자리(공개): `ctx.tradingview?.(container, 'mini-symbol-overview', { symbol: TV_SYMBOLS.kospi })` — 형은 `import type` 로만(값 import 는 eslint 가 막는다).
- 품질(§6.7): `p.setSource(env, invalidRows)`(원천 줄·stale 머리·notes), 값은 `qValue(text, quality, asOf)`(잠정 배지·밑줄, invalid 는 null), 행은 `visibleRows(rows)`, 검산은 `checkLine(label, residual | null, { reason })`(null 이면 '검산 불가: 사유' — 0 으로 그리지 않는다).
- 숫자: API 금액은 원 단위 정수 — `ui/fmt.ts` 의 `eok`·`joEok`·`pct`·`num` 으로 **화면에서만** 반올림. 시각은 `kstTime`·`kstStamp`(늘 KST).
- 오류: `p.showError(err)` — 그 패널에만, 응답 본문 없이. 주기 갱신은 `ctx.every(ms, fn)`(탭 숨김 멈춤·앱 내릴 때 정리).
- 금지(eslint·검사로 막는다): `innerHTML` 등 문자열 HTML, `style` 속성(로그인 CSP `style-src 'self'` — 동적 위치·폭은 `h(..., { css: {...} })` 로), `data/api`·`data/auth`·`pages/tradingview` 값 import, 공개 번들에 API 경로 문자열.
- 시험: `test/pages/**`(W2). A 의 합성 응답 픽스처 `test/fixtures/api/*.json`, 공개 픽스처 `test/fixtures/public/*.json`. 도우미는 `test/helpers.ts`(가짜 fetch·시계·저장소·페이지 로더).

## 의존성·lock

- dev 의존성: `vite`·`typescript`·`vitest`·`jsdom`·`eslint`·`@eslint/js`·`typescript-eslint`·`axe-core`·`openapi-typescript`(9개, §6.1). vitest 는 보안 공지(GHSA-82fw-gwwq-j7x9·GHSA-5gmw-xhrv-c9v3) 때문에 3.x 대신 **4.1.11**, eslint 는 9.x 지원 종료로 **10.x**.
- `package-lock.json` 은 **npm 11** 로 만들었다(`npx npm@11 install --ignore-scripts`). node 22 의 npm 10.9 는 vitest 4 의 peer 묶음에서 lock 을 새로 만들 때 멈춘다(arborist `edgesOut` 오류) — `npm ci` 는 npm 10 으로도 된다(확인함).
- 자동 갱신 봇은 두지 않는다(예약 실행 금지 — R12).
