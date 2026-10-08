#!/usr/bin/env node
// 빌드 결과 검사 — 공개 번들에 로그인 API 흔적이 없는지, 로그인 번들에 TradingView 가 없는지(docs/p3_design.md §6.6).
//
//   node scripts/check-bundle.mjs                 # dist-public 과 dist-login 둘 다(둘 다 있어야 한다)
//   node scripts/check-bundle.mjs dist-public     # 하나만
//
// 공개(dist-public): '/api'·'/telegram'·'auth/login'·'X-KBJ-CSRF'·'KBJ_'·'kbj_session' 문자열 0 — 하나라도 있으면 실패.
//   선택: 환경변수 KBJ_LOGIN_ORIGIN(로그인 VM 주소)을 주면 그 문자열도 0 이어야 한다(값은 출력하지 않는다).
// 로그인(dist-login): TradingView 주소('tradingview.com'·'tradingview-widget.com'·'embed-widget-') 0(대소문자 무시).
// 크기 예산(§6.9 [제안]): JS gzip 공개 ≤ 60 KB·로그인 ≤ 90 KB, CSS gzip ≤ 20 KB. 서비스 워커(sw.js)는 JS 합계에 넣지 않는다.
// 종료 코드: 0 통과, 1 발견, 2 사용법·빌드 없음.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { gzipSync } from 'node:zlib';

export const PUBLIC_FORBIDDEN = [
  { id: 'api_path', needle: '/api', why: '로그인 API 경로' },
  { id: 'telegram_path', needle: '/telegram', why: '텔레그램 웹훅 경로' },
  { id: 'auth_login', needle: 'auth/login', why: '로그인 엔드포인트' },
  { id: 'csrf_header', needle: 'x-kbj-csrf', why: 'CSRF 헤더 이름', ci: true },
  { id: 'env_name', needle: 'KBJ_', why: 'KBJ 환경변수 이름' },
  { id: 'session_cookie', needle: 'kbj_session', why: '세션 쿠키 이름', ci: true },
];

export const LOGIN_FORBIDDEN = [
  { id: 'tradingview_host', needle: 'tradingview.com', why: 'TradingView 주소(공개판 전용)', ci: true },
  { id: 'tradingview_widget_host', needle: 'tradingview-widget.com', why: 'TradingView 위젯 주소(공개판 전용)', ci: true },
  { id: 'tradingview_embed', needle: 'embed-widget-', why: 'TradingView 임베드 스크립트(공개판 전용)', ci: true },
];

export const BUDGET_GZIP = {
  public: { js: 60 * 1024, css: 20 * 1024 },
  login: { js: 90 * 1024, css: 20 * 1024 },
};

const TEXT_EXT = /\.(?:js|mjs|css|html|json|webmanifest|svg|txt|map|xml)$/i;

/**
 * 파일 하나의 글자에서 금지 문자열을 찾는다. 결과에는 찾은 값이 아니라 규칙·줄 번호만 담는다.
 * @param {string} name
 * @param {string} text
 * @param {{id: string, needle: string, why: string, ci?: boolean}[]} rules
 */
export function scanText(name, text, rules) {
  const out = [];
  const lines = text.split('\n');
  for (const r of rules) {
    const needle = r.ci ? r.needle.toLowerCase() : r.needle;
    lines.forEach((line, i) => {
      const hay = r.ci ? line.toLowerCase() : line;
      if (hay.includes(needle)) out.push({ file: name, line: i + 1, rule: r.id, why: r.why });
    });
  }
  return out;
}

/**
 * @param {{name: string, bytes: number}[]} files gzip 크기
 * @param {{js: number, css: number}} budget
 */
export function checkBudget(files, budget) {
  const sum = (re) => files.filter((f) => re.test(f.name) && !/(^|\/)sw\.js$/.test(f.name)).reduce((a, f) => a + f.bytes, 0);
  const js = sum(/\.m?js$/);
  const css = sum(/\.css$/);
  const out = [];
  if (js > budget.js) out.push({ rule: 'budget_js', why: `JS gzip ${js} B > ${budget.js} B` });
  if (css > budget.css) out.push({ rule: 'budget_css', why: `CSS gzip ${css} B > ${budget.css} B` });
  return { js, css, findings: out };
}

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

/** 빌드 디렉터리 하나를 검사한다. */
export function checkDir(dir, kind, extraNeedles = []) {
  const rules = kind === 'public' ? [...PUBLIC_FORBIDDEN, ...extraNeedles] : LOGIN_FORBIDDEN;
  const findings = [];
  const sizes = [];
  for (const p of walk(dir)) {
    const rel = relative(dir, p).split('\\').join('/');
    if (!TEXT_EXT.test(rel)) continue;
    const buf = readFileSync(p);
    findings.push(...scanText(rel, buf.toString('utf8'), rules));
    sizes.push({ name: rel, bytes: gzipSync(buf).length });
  }
  const budget = checkBudget(sizes, BUDGET_GZIP[kind]);
  return { findings: [...findings, ...budget.findings.map((f) => ({ file: '(합계)', line: 0, ...f }))], js: budget.js, css: budget.css };
}

function kindOf(dir) {
  if (/dist-public\/?$/.test(dir)) return 'public';
  if (/dist-login\/?$/.test(dir)) return 'login';
  return null;
}

function main(argv) {
  const here = resolve(fileURLToPath(new URL('..', import.meta.url)));
  const dirs = argv.length ? argv : ['dist-public', 'dist-login'];
  const extra = [];
  const origin = process.env.KBJ_LOGIN_ORIGIN;
  if (origin) extra.push({ id: 'login_origin', needle: origin, why: '로그인 VM 주소' });
  let bad = 0;
  for (const d of dirs) {
    const kind = kindOf(d);
    if (!kind) {
      console.error(`사용법: check-bundle.mjs [dist-public] [dist-login] — 모르는 디렉터리 이름: ${d}`);
      return 2;
    }
    const abs = resolve(here, d);
    if (!existsSync(abs)) {
      console.error(`${d}: 빌드 결과가 없다 — 먼저 npm run build:${kind}`);
      return 2;
    }
    const r = checkDir(abs, kind, extra);
    for (const f of r.findings) console.error(`${d}/${f.file}:${f.line}: ${f.rule} — ${f.why}`);
    console.log(`${d}: 금지 문자열·예산 위반 ${r.findings.length}건 (JS gzip ${r.js} B, CSS gzip ${r.css} B)`);
    bad += r.findings.length;
  }
  return bad ? 1 : 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exit(main(process.argv.slice(2)));
}
