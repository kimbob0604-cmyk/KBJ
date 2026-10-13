#!/usr/bin/env node
// 공개 데이터 합치기 — public-data 브랜치의 JSON 을 공개 빌드(dist-public/data/)에 넣는다(docs/p3_design.md §7.3, docs/p4_design.md §1.9·§7).
//
//   node scripts/merge-public-data.mjs <public-data 디렉터리> [dist-public]
//
// 규칙(하나라도 어기면 아무것도 복사하지 않고 종료 코드 1):
// - manifest.json 의 files 에 적힌 파일만 복사한다(이름 허용 목록). 이름 형식 ^[a-z0-9_]+\.json$ — 경로·숨김 파일 불가.
// - 항목마다 source·as_of(시간대 포함 ISO)·quality(ok·stale·estimated·invalid) 필수(절대 규칙 1).
// - source 에 로그인 등급 출처 이름(KIS·KRX·ETF_ISSUERS·YAHOO·FSC 시세·컨센서스·네이버)이 있으면 실패(§7.4 내용 검사).
// - 파일 안의 모든 source 값(봉투·행 — 깊이 무관)도 같은 검사. JSON 으로 읽히지 않으면 실패.
// - P4 하위 폴더(manifest 의 dirs 항목 — fin·reports): 파일마다 적지 않고 폴더마다 개수·합계 크기·요약 해시(dirDigest)를
//   맞춘다. 이름은 fin/<코드>.json·reports/<코드>.json·reports/<코드>.xlsx 만(코드 ^[0-9A-Z]{6}$ — web/src/data/static.ts 의
//   PUBLIC_SUBDIR·PUBLIC_DOWNLOAD 와 같다). JSON 은 파일 자체가 봉투(source·as_of·quality), 엑셀은 짝 JSON 의
//   data.xlsx_sha256 과 같아야 한다.
// - DART 전용 파일(P4 공시 목록·corp_index·하위 폴더 전부): 모든 source 가 정확히 'DART', 관심종목 흔적(이름에 watch 가 든
//   키·'prv_' 글자) 0(§7 — 공개 파일에 관심종목 표식을 넣지 않는다).
// - manifest 에 없는 파일·폴더는 복사하지 않고 목록만 알린다.
// 규칙은 파이썬 kbj/services/public_export/manifest.py 와 같게 둔다(tests/unit/public_export/test_manifest.py 가 대조).
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const NAME_RE = /^[a-z0-9_]+\.json$/;
export const SUBDIRS = ['fin', 'reports'];
export const SUBDIR_JSON_RE = /^(fin|reports)\/[0-9A-Z]{6}\.json$/;
export const SUBDIR_XLSX_RE = /^reports\/[0-9A-Z]{6}\.xlsx$/;
/** P4 공시·재무 목록 파일 — 원천이 DART 하나(manifest.py DART_ONLY_NAMES 와 같은 목록) */
export const DART_ONLY = new Set([
  'filings_feed.json',
  'earnings_prelim.json',
  'overhang_recent.json',
  'insider_recent.json',
  'earnings_schedule.json',
  'corp_index.json',
]);
export const QUALITIES = ['ok', 'stale', 'estimated', 'invalid'];
const AWARE_ISO = /^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})$/;
/** 로그인 등급 출처 이름(DATA_TIERS §1) — source 문자열에 단어로 나오면 안 된다 */
export const LOGIN_SOURCES = /\b(KIS|KRX|ETF_ISSUERS|YAHOO|FSC_STOCK|FSC_INDEX|FNGUIDE|CONSENSUS|NAVER)\b/i;
const WATCH_KEY = /watch/i;

function manifestBody(manifest) {
  const body = manifest && typeof manifest === 'object' && 'data' in manifest && 'source' in manifest ? manifest.data : manifest;
  if (!body || typeof body !== 'object') throw new Error('manifest 형식 오류');
  return body;
}

/** manifest → 항목 목록(모양 두 가지: files 배열 또는 이름→메타 객체, 봉투로 싸였으면 data 안). */
export function manifestEntries(manifest) {
  const f = manifestBody(manifest).files;
  if (Array.isArray(f)) return f.map((e) => ({ ...e, name: String(e?.name ?? '') }));
  if (f && typeof f === 'object') return Object.entries(f).map(([name, e]) => ({ ...e, name }));
  throw new Error('manifest 에 files 가 없음');
}

/** manifest → 하위 폴더 항목(P3 manifest 처럼 없으면 빈 목록). */
export function manifestDirs(manifest) {
  const d = manifestBody(manifest).dirs;
  if (d === undefined || d === null) return [];
  if (!Array.isArray(d)) throw new Error('manifest 의 dirs 가 목록이 아님');
  return d.map((e) => ({ ...e, name: String(e?.name ?? '') }));
}

/**
 * JSON 안의 모든 `source` 값(봉투·행·칸 — 깊이 무관)에서 로그인 등급 출처 이름을 찾는다. 값이 아니라 JSON 경로만 돌려준다.
 * @param {unknown} v
 * @param {string} [path]
 * @returns {string[]}
 */
export function loginSourcePaths(v, path = '$') {
  const out = [];
  if (Array.isArray(v)) {
    v.forEach((x, i) => out.push(...loginSourcePaths(x, `${path}[${i}]`)));
  } else if (v && typeof v === 'object') {
    for (const [k, x] of Object.entries(v)) {
      const p = `${path}.${k}`;
      if (k === 'source' && typeof x === 'string' && LOGIN_SOURCES.test(x)) out.push(p);
      else out.push(...loginSourcePaths(x, p));
    }
  }
  return out;
}

/**
 * 모든 `source` 값이 정확히 'DART' 인지 — 아닌 곳의 경로만(값 없음).
 * @param {unknown} v
 * @param {string} [path]
 * @returns {string[]}
 */
export function nonDartSourcePaths(v, path = '$') {
  const out = [];
  if (Array.isArray(v)) {
    v.forEach((x, i) => out.push(...nonDartSourcePaths(x, `${path}[${i}]`)));
  } else if (v && typeof v === 'object') {
    for (const [k, x] of Object.entries(v)) {
      const p = `${path}.${k}`;
      if (k === 'source') {
        if (x !== 'DART') out.push(p);
      } else out.push(...nonDartSourcePaths(x, p));
    }
  }
  return out;
}

/**
 * 관심종목 흔적 — 이름에 watch 가 든 키의 경로.
 * @param {unknown} v
 * @param {string} [path]
 * @returns {string[]}
 */
export function watchTracePaths(v, path = '$') {
  const out = [];
  if (Array.isArray(v)) {
    v.forEach((x, i) => out.push(...watchTracePaths(x, `${path}[${i}]`)));
  } else if (v && typeof v === 'object') {
    for (const [k, x] of Object.entries(v)) {
      const p = `${path}.${k}`;
      if (WATCH_KEY.test(k)) out.push(p);
      out.push(...watchTracePaths(x, p));
    }
  }
  return out;
}

/** @param {string | Uint8Array} data */
function bytesOf(data) {
  return typeof data === 'string' ? Buffer.from(data, 'utf8') : Buffer.from(data);
}

/** @param {string | Uint8Array} data */
function textOf(data) {
  return typeof data === 'string' ? data : Buffer.from(data).toString('utf8');
}

/**
 * sha256(16진) — 글자는 UTF-8 바이트로.
 * @param {string | Uint8Array} data
 */
export function sha256(data) {
  return createHash('sha256').update(bytesOf(data)).digest('hex');
}

/**
 * 하위 폴더 요약 해시 — (경로, sha256) 들을 경로 순으로 `경로\t해시\n` 줄로 이어 sha256(manifest.py dir_digest 와 같다).
 * @param {[string, string][]} items
 */
export function dirDigest(items) {
  const sorted = [...items].sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return sha256(sorted.map(([p, h]) => `${p}\t${h}\n`).join(''));
}

function fileName(name) {
  return name.endsWith('.json') ? name : `${name}.json`;
}

/** DART 전용 파일 검사(원천 DART 하나·관심종목 흔적·로그인 스키마 이름). */
function dartOnlyProblems(name, text, parsed) {
  const out = [];
  const nd = nonDartSourcePaths(parsed);
  if (nd.length) out.push(`${name}: source 가 DART 가 아님(${nd.slice(0, 3).join(', ')})`);
  const w = watchTracePaths(parsed);
  if (w.length) out.push(`${name}: 관심종목 흔적 키(${w.slice(0, 3).join(', ')})`);
  if (text.includes('prv_')) out.push(`${name}: 로그인 등급 스키마 이름('prv_' 규칙)`);
  return out;
}

/** 하위 폴더 JSON 한 파일(봉투) 검사 — 문제 목록과 (reports 면) 짝 엑셀 해시. */
function checkSubdirJson(p, data) {
  const errors = [];
  const text = textOf(data);
  let env;
  try {
    env = JSON.parse(text);
  } catch {
    return { errors: [`${p}: JSON 이 아님`], claim: null };
  }
  if (!env || typeof env !== 'object' || Array.isArray(env)) return { errors: [`${p}: 봉투가 아님`], claim: null };
  if (typeof env.source !== 'string' || !env.source) errors.push(`${p}: source 없음`);
  if (typeof env.as_of !== 'string' || !AWARE_ISO.test(env.as_of)) errors.push(`${p}: as_of 없음·시간대 없음`);
  if (!QUALITIES.includes(env.quality)) errors.push(`${p}: quality 없음·모르는 값`);
  if (!('data' in env)) errors.push(`${p}: data 없음`);
  const bad = loginSourcePaths(env);
  if (bad.length) errors.push(`${p}: source 에 로그인 등급 출처 이름(${bad.slice(0, 3).join(', ')})`);
  errors.push(...dartOnlyProblems(p, text, env));
  const claim = env.data && typeof env.data === 'object' ? env.data.xlsx_sha256 : undefined;
  return { errors, claim: typeof claim === 'string' ? claim : null };
}

/** 하위 폴더 하나 검사 — 복사할 경로들과 문제 목록. */
function planDir(entry, files) {
  const errors = [];
  const name = entry.name;
  const ok = [];
  const inDir = Object.keys(files)
    .filter((k) => k.startsWith(`${name}/`))
    .sort();
  for (const p of inDir) {
    if (SUBDIR_JSON_RE.test(p) || SUBDIR_XLSX_RE.test(p)) ok.push(p);
    else errors.push(`${p}: 허용하지 않는 이름(하위 폴더)`);
  }
  const claims = new Map(); // 엑셀 경로 → 짝 JSON 의 xlsx_sha256
  const hashes = [];
  let bytes = 0;
  for (const p of ok) {
    const body = bytesOf(files[p]);
    bytes += body.length;
    hashes.push([p, sha256(body)]);
    if (p.endsWith('.xlsx')) continue;
    const r = checkSubdirJson(p, files[p]);
    errors.push(...r.errors);
    if (p.startsWith('reports/')) claims.set(p.replace(/\.json$/, '.xlsx'), r.claim);
  }
  const sha = new Map(hashes);
  for (const p of ok) {
    if (!p.endsWith('.xlsx')) continue;
    const claim = claims.get(p);
    if (!claim) errors.push(`${p}: 짝 JSON 의 xlsx_sha256 이 없음`);
    else if (claim !== sha.get(p)) errors.push(`${p}: 짝 JSON 의 xlsx_sha256 과 다름`);
  }
  for (const [xl, claim] of claims) if (claim && !sha.has(xl)) errors.push(`${xl}: 짝 JSON 은 엑셀이 있다는데 파일이 없음`);
  if (entry.count !== ok.length) errors.push(`${name}/: manifest 개수와 다름`);
  if (entry.bytes !== bytes) errors.push(`${name}/: manifest 합계 크기와 다름`);
  if (entry.digest !== dirDigest(hashes)) errors.push(`${name}/: manifest 요약 해시와 다름`);
  if (entry.source !== 'DART') errors.push(`${name}/: source 가 DART 가 아님`);
  if (typeof entry.as_of !== 'string' || !AWARE_ISO.test(entry.as_of)) errors.push(`${name}/: as_of 없음·시간대 없음`);
  if (!QUALITIES.includes(entry.quality)) errors.push(`${name}/: quality 없음·모르는 값`);
  return { copy: ok, errors };
}

/**
 * 복사 계획(파일 시스템 없이 — 시험용).
 * @param {unknown} manifest
 * @param {Record<string, string | Uint8Array>} files 데이터 루트 기준 경로(평평한 이름 또는 fin/<코드>.json 등) → 글자·바이트
 */
export function planMerge(manifest, files) {
  const errors = [];
  const copy = [];
  let entries;
  let dirs;
  try {
    entries = manifestEntries(manifest);
    dirs = manifestDirs(manifest);
  } catch (e) {
    return { copy, errors: [String(e instanceof Error ? e.message : e)], ignored: [] };
  }
  for (const e of entries) {
    const name = fileName(e.name);
    if (!NAME_RE.test(name) || name === 'manifest.json') {
      errors.push(`${e.name || '(이름 없음)'}: 허용하지 않는 이름`);
      continue;
    }
    if (typeof e.source !== 'string' || !e.source) errors.push(`${name}: source 없음`);
    else if (LOGIN_SOURCES.test(e.source)) errors.push(`${name}: 로그인 등급 출처 이름이 source 에 있음`);
    if (typeof e.as_of !== 'string' || !AWARE_ISO.test(e.as_of)) errors.push(`${name}: as_of 없음·시간대 없음`);
    if (!QUALITIES.includes(e.quality)) errors.push(`${name}: quality 없음·모르는 값`);
    if (!(name in files)) {
      errors.push(`${name}: manifest 에 있으나 파일이 없음`);
      continue;
    }
    const text = textOf(files[name]);
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      errors.push(`${name}: JSON 이 아님`);
      continue;
    }
    const bad = loginSourcePaths(parsed);
    if (bad.length) errors.push(`${name}: source 에 로그인 등급 출처 이름(${bad.slice(0, 3).join(', ')})`);
    if (DART_ONLY.has(name)) errors.push(...dartOnlyProblems(name, text, parsed));
    copy.push(name);
  }
  const seen = new Set();
  for (const d of dirs) {
    if (!SUBDIRS.includes(d.name) || seen.has(d.name)) {
      errors.push(`${d.name || '(이름 없음)'}/: 허용하지 않는(또는 겹친) 하위 폴더`);
      continue;
    }
    seen.add(d.name);
    const plan = planDir(d, files);
    errors.push(...plan.errors);
    copy.push(...plan.copy);
  }
  const listed = new Set([...copy, 'manifest.json']);
  const ignored = Object.keys(files).filter((n) => !listed.has(n));
  return { copy: errors.length ? [] : [...copy, 'manifest.json'], errors, ignored };
}

/**
 * 데이터 디렉터리 → { 경로: 글자(.json)·바이트(그 밖) } — 평평한 *.json + 하위 폴더(fin·reports)의 파일.
 * @param {string} srcAbs
 */
export function readDataDir(srcAbs) {
  const files = {};
  for (const n of readdirSync(srcAbs)) {
    const p = join(srcAbs, n);
    if (n.endsWith('.json') && statSync(p).isFile()) files[n] = readFileSync(p, 'utf8');
  }
  for (const sub of SUBDIRS) {
    const dir = join(srcAbs, sub);
    if (!existsSync(dir) || !statSync(dir).isDirectory()) continue;
    for (const n of readdirSync(dir)) {
      const p = join(dir, n);
      if (statSync(p).isFile()) files[`${sub}/${n}`] = n.endsWith('.json') ? readFileSync(p, 'utf8') : readFileSync(p);
    }
  }
  return files;
}

function main(argv) {
  const [src, dist = 'dist-public'] = argv;
  if (!src) {
    console.error('사용법: merge-public-data.mjs <public-data 디렉터리> [dist-public]');
    return 2;
  }
  const srcAbs = resolve(src);
  const distAbs = resolve(dist);
  if (!existsSync(join(srcAbs, 'manifest.json'))) {
    console.error(`${src}: manifest.json 이 없다`);
    return 2;
  }
  if (!existsSync(join(distAbs, 'index.html'))) {
    console.error(`${dist}: 공개 빌드가 없다 — 먼저 npm run build:public`);
    return 2;
  }
  const files = readDataDir(srcAbs);
  let manifest;
  try {
    manifest = JSON.parse(files['manifest.json']);
  } catch {
    console.error('manifest.json 이 JSON 이 아니다');
    return 1;
  }
  const plan = planMerge(manifest, files);
  for (const e of plan.errors) console.error(`실패: ${e}`);
  for (const n of plan.ignored) console.warn(`건너뜀(manifest 에 없음): ${n}`);
  if (plan.errors.length) return 1;
  const out = join(distAbs, 'data');
  for (const n of plan.copy) {
    const dest = join(out, n);
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(join(srcAbs, n), dest);
  }
  const sub = plan.copy.filter((n) => n.includes('/')).length;
  console.log(`복사 ${plan.copy.length}개(하위 폴더 ${sub}개) → ${dist}/data/`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exit(main(process.argv.slice(2)));
}
