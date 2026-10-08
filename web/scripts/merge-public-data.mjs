#!/usr/bin/env node
// 공개 데이터 합치기 — public-data 브랜치의 JSON 을 공개 빌드(dist-public/data/)에 넣는다(docs/p3_design.md §7.3).
//
//   node scripts/merge-public-data.mjs <public-data 디렉터리> [dist-public]
//
// 규칙(하나라도 어기면 아무것도 복사하지 않고 종료 코드 1):
// - manifest.json 에 적힌 파일만 복사한다(이름 허용 목록). 이름 형식 ^[a-z0-9_]+\.json$ — 경로·숨김 파일 불가.
// - 항목마다 source·as_of(시간대 포함 ISO)·quality(ok·stale·estimated·invalid) 필수(절대 규칙 1).
// - source 에 로그인 등급 출처 이름(KIS·KRX·ETF_ISSUERS·YAHOO·FSC 시세·컨센서스·네이버)이 있으면 실패(§7.4 내용 검사).
// - 파일 안의 모든 source 값(봉투·행 — 깊이 무관)도 같은 검사. JSON 으로 읽히지 않으면 실패.
// - manifest 에 없는 파일은 복사하지 않고 목록만 알린다.
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const NAME_RE = /^[a-z0-9_]+\.json$/;
export const QUALITIES = ['ok', 'stale', 'estimated', 'invalid'];
const AWARE_ISO = /^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})$/;
/** 로그인 등급 출처 이름(DATA_TIERS §1) — source 문자열에 단어로 나오면 안 된다 */
export const LOGIN_SOURCES = /\b(KIS|KRX|ETF_ISSUERS|YAHOO|FSC_STOCK|FSC_INDEX|FNGUIDE|CONSENSUS|NAVER)\b/i;

/** manifest → 항목 목록(모양 두 가지: files 배열 또는 이름→메타 객체, 봉투로 싸였으면 data 안). */
export function manifestEntries(manifest) {
  const body = manifest && typeof manifest === 'object' && 'data' in manifest && 'source' in manifest ? manifest.data : manifest;
  if (!body || typeof body !== 'object') throw new Error('manifest 형식 오류');
  const f = body.files;
  if (Array.isArray(f)) return f.map((e) => ({ ...e, name: String(e?.name ?? '') }));
  if (f && typeof f === 'object') return Object.entries(f).map(([name, e]) => ({ ...e, name }));
  throw new Error('manifest 에 files 가 없음');
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

function fileName(name) {
  return name.endsWith('.json') ? name : `${name}.json`;
}

/**
 * 복사 계획(파일 시스템 없이 — 시험용).
 * @param {unknown} manifest
 * @param {Record<string, string>} files 디렉터리의 파일 이름 → 글자
 */
export function planMerge(manifest, files) {
  const errors = [];
  const copy = [];
  let entries;
  try {
    entries = manifestEntries(manifest);
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
    let parsed;
    try {
      parsed = JSON.parse(files[name]);
    } catch {
      errors.push(`${name}: JSON 이 아님`);
      continue;
    }
    const bad = loginSourcePaths(parsed);
    if (bad.length) errors.push(`${name}: source 에 로그인 등급 출처 이름(${bad.slice(0, 3).join(', ')})`);
    copy.push(name);
  }
  const listed = new Set([...copy, 'manifest.json']);
  const ignored = Object.keys(files).filter((n) => !listed.has(n));
  return { copy: errors.length ? [] : [...copy, 'manifest.json'], errors, ignored };
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
  const files = {};
  for (const n of readdirSync(srcAbs)) if (n.endsWith('.json')) files[n] = readFileSync(join(srcAbs, n), 'utf8');
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
  mkdirSync(out, { recursive: true });
  for (const n of plan.copy) copyFileSync(join(srcAbs, n), join(out, n));
  console.log(`복사 ${plan.copy.length}개 → ${dist}/data/`);
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exit(main(process.argv.slice(2)));
}
