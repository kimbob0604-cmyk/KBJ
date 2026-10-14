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
// - P5 공개 파일(docs/p5_design.md §7): 하위 폴더 macro/<이름>.json(소문자 이름, 파일 자체가 봉투)과
//   평평한 credit.json. 원천이 공개 출처 여럿이라 DART 전용이 아니고(폴더 source 는 원천들을 쉼표로 이은 것), 관심종목 흔적·'prv_'
//   글자는 여기서도 0. 크기 상한 DIR_CAPS(폴더 합계)·FILE_CAPS(파일 하나) — 넘으면 실패(자르지 않는다).
// - P6 공개 파일(docs/p6_design.md §7): 하위 폴더 trade/ — 평평한 trade/<소문자>.json 과 한 단계 더
//   trade/(hs|country|region|stock)/<코드 2~10자>.json. 코드 파일 상한은 PREFIX_CAPS. 'origin: local'(VM 관심종목 매핑) 0,
//   (manifest.py trade_problems 와 같다).
// - P9 원시세 칸(docs/p9_design.md §3.2 price_fields, ADR 0035): 모든 공개 JSON 에 PRICE_KEYS 키 0 — 예외는
//   PRICE_FIELD_ALLOW(파일·키·값 모양)뿐(manifest.py price_problems·PRICE_FIELD_ALLOW 와 같다 — 시험).
// - P7 공개 파일(docs/p7_design.md §7): 하위 폴더 diag/<소문자>.json(공공지표 진단·게이지·이벤트). 로그인 판정 키
//   (fear_greed·verdict·gex*) 0(manifest.py diag_problems 와 같다).
// - manifest 에 없는 파일·폴더는 복사하지 않고 목록만 알린다.
// 규칙은 파이썬 kbj/services/public_export/manifest.py 와 같게 둔다(tests/unit/public_export/test_manifest.py 가 대조).
import { createHash } from "node:crypto";
import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const NAME_RE = /^[a-z0-9_]+\.json$/;
export const SUBDIRS = ["fin", "reports", "macro", "trade", "diag"];
/** P4 하위 폴더 — 원천이 DART 하나(manifest.py DART_SUBDIRS) */
export const DART_SUBDIRS = ["fin", "reports"];
export const SUBDIR_JSON_RE =
  /^(?:(fin|reports)\/[0-9A-Z]{6}|(macro|diag)\/[a-z0-9_]+|(trade)\/(?:(?:hs|country|region|stock)\/[0-9A-Z]{2,10}|[a-z0-9_]+))\.json$/;
export const SUBDIR_XLSX_RE = /^reports\/[0-9A-Z]{6}\.xlsx$/;
/** P4 공시·재무 목록 파일 — 원천이 DART 하나(manifest.py DART_ONLY_NAMES 와 같은 목록) */
export const DART_ONLY = new Set([
  "filings_feed.json",
  "earnings_prelim.json",
  "overhang_recent.json",
  "insider_recent.json",
  "earnings_schedule.json",
  "corp_index.json",
]);
/** P5 평평한 공개 파일 — 관심종목 흔적·로그인 스키마 이름 검사를 받는다(manifest.py P5_FLAT_NAMES) */
export const P5_FLAT_NAMES = new Set(["credit.json"]);
const KB = 1024;
/** 크기 상한(바이트, p5·p6_design §7 [제안]) — manifest.py DIR_CAPS·FILE_CAPS·PREFIX_CAPS 와 같다 */
export const DIR_CAPS = {
  macro: 3 * KB * KB,
  trade: 20 * KB * KB,
  diag: 1 * KB * KB,
};
export const FILE_CAPS = {
  "credit.json": 100 * KB,
  "macro/calendar.json": 200 * KB,
  "trade/flash.json": 200 * KB,
  "trade/ribbon.json": 4 * KB,
  "trade/hs2.json": 1024 * KB,
  "trade/countries.json": 1024 * KB,
  "trade/surge.json": 1024 * KB,
  "trade/items104.json": 1024 * KB,
  "trade/stocks.json": 1024 * KB,
  "diag/summary.json": 100 * KB,
  "diag/history.json": 512 * KB,
  "diag/events.json": 100 * KB,
};
/** 코드 이름 파일(trade/ 한 단계 더) — 경로 접두 → 파일 하나 상한 */
export const PREFIX_CAPS = {
  "trade/hs/": 1024 * KB,
  "trade/country/": 1024 * KB,
  "trade/region/": 200 * KB,
  "trade/stock/": 100 * KB,
};
/** 원시세 칸 — 공개 JSON 어디에도 없어야 하는 키(P9 price_fields — 옛 trade/stock 규칙 D-P6-4 를 넓혔다) */
export const PRICE_KEYS = new Set([
  "close",
  "open",
  "high",
  "low",
  "price",
  "volume",
  "turnover",
  "mktcap",
  "nav",
  "net_value",
  "chg_pct",
]);
/** 원시세 칸 예외 — [파일, 키, 값 모양('hhmm'|'number'), 사유]. manifest.py PRICE_FIELD_ALLOW 와 같다(시험) */
export const PRICE_FIELD_ALLOW = [
  [
    "calendar.json",
    "open",
    "hhmm",
    "장 시작 시각(HH:MM) — 가격 아님(KBJ 거래 캘린더 코드 계산)",
  ],
  [
    "calendar.json",
    "close",
    "hhmm",
    "장 마감 시각(HH:MM) — 가격 아님(KBJ 거래 캘린더 코드 계산)",
  ],
  [
    "overhang_recent.json",
    "price",
    "number",
    "DART 주요사항보고서의 전환·행사·발행가액(공개 출처)",
  ],
];
export const QUALITIES = ["ok", "stale", "estimated", "invalid"];
const AWARE_ISO = /^\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})$/;
/** 로그인 등급 출처 이름(DATA_TIERS §1) — source 문자열에 단어로 나오면 안 된다 */
export const LOGIN_SOURCES =
  /\b(KIS|KRX|ETF_ISSUERS|YAHOO|FSC_STOCK|FSC_INDEX|FNGUIDE|CONSENSUS|NAVER)\b/i;
const WATCH_KEY = /watch/i;

function manifestBody(manifest) {
  const body =
    manifest &&
    typeof manifest === "object" &&
    "data" in manifest &&
    "source" in manifest
      ? manifest.data
      : manifest;
  if (!body || typeof body !== "object") throw new Error("manifest 형식 오류");
  return body;
}

/** manifest → 항목 목록(모양 두 가지: files 배열 또는 이름→메타 객체, 봉투로 싸였으면 data 안). */
export function manifestEntries(manifest) {
  const f = manifestBody(manifest).files;
  if (Array.isArray(f))
    return f.map((e) => ({ ...e, name: String(e?.name ?? "") }));
  if (f && typeof f === "object")
    return Object.entries(f).map(([name, e]) => ({ ...e, name }));
  throw new Error("manifest 에 files 가 없음");
}

/** manifest → 하위 폴더 항목(P3 manifest 처럼 없으면 빈 목록). */
export function manifestDirs(manifest) {
  const d = manifestBody(manifest).dirs;
  if (d === undefined || d === null) return [];
  if (!Array.isArray(d)) throw new Error("manifest 의 dirs 가 목록이 아님");
  return d.map((e) => ({ ...e, name: String(e?.name ?? "") }));
}

/**
 * JSON 안의 모든 `source` 값(봉투·행·칸 — 깊이 무관)에서 로그인 등급 출처 이름을 찾는다. 값이 아니라 JSON 경로만 돌려준다.
 * @param {unknown} v
 * @param {string} [path]
 * @returns {string[]}
 */
export function loginSourcePaths(v, path = "$") {
  const out = [];
  if (Array.isArray(v)) {
    v.forEach((x, i) => out.push(...loginSourcePaths(x, `${path}[${i}]`)));
  } else if (v && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      const p = `${path}.${k}`;
      if (k === "source" && typeof x === "string" && LOGIN_SOURCES.test(x))
        out.push(p);
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
export function nonDartSourcePaths(v, path = "$") {
  const out = [];
  if (Array.isArray(v)) {
    v.forEach((x, i) => out.push(...nonDartSourcePaths(x, `${path}[${i}]`)));
  } else if (v && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      const p = `${path}.${k}`;
      if (k === "source") {
        if (x !== "DART") out.push(p);
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
export function watchTracePaths(v, path = "$") {
  const out = [];
  if (Array.isArray(v)) {
    v.forEach((x, i) => out.push(...watchTracePaths(x, `${path}[${i}]`)));
  } else if (v && typeof v === "object") {
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
  return typeof data === "string"
    ? Buffer.from(data, "utf8")
    : Buffer.from(data);
}

/** @param {string | Uint8Array} data */
function textOf(data) {
  return typeof data === "string" ? data : Buffer.from(data).toString("utf8");
}

/**
 * sha256(16진) — 글자는 UTF-8 바이트로.
 * @param {string | Uint8Array} data
 */
export function sha256(data) {
  return createHash("sha256").update(bytesOf(data)).digest("hex");
}

/**
 * 하위 폴더 요약 해시 — (경로, sha256) 들을 경로 순으로 `경로\t해시\n` 줄로 이어 sha256(manifest.py dir_digest 와 같다).
 * @param {[string, string][]} items
 */
export function dirDigest(items) {
  const sorted = [...items].sort((a, b) =>
    a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0,
  );
  return sha256(sorted.map(([p, h]) => `${p}\t${h}\n`).join(""));
}

function fileName(name) {
  return name.endsWith(".json") ? name : `${name}.json`;
}

/** 관심종목 흔적·로그인 스키마 이름 검사(DART 전용 파일·하위 폴더 전부·P5 평평한 파일). */
function traceProblems(name, text, parsed) {
  const out = [];
  const w = watchTracePaths(parsed);
  if (w.length)
    out.push(`${name}: 관심종목 흔적 키(${w.slice(0, 3).join(", ")})`);
  if (text.includes("prv_"))
    out.push(`${name}: 로그인 등급 스키마 이름('prv_' 규칙)`);
  return out;
}

/** DART 전용 파일 검사(원천 DART 하나 + 관심종목 흔적·로그인 스키마 이름). */
function dartOnlyProblems(name, text, parsed) {
  const out = [];
  const nd = nonDartSourcePaths(parsed);
  if (nd.length)
    out.push(`${name}: source 가 DART 가 아님(${nd.slice(0, 3).join(", ")})`);
  return [...out, ...traceProblems(name, text, parsed)];
}

/** 파일 하나 상한(바이트) — FILE_CAPS 이름 또는 PREFIX_CAPS 접두(manifest.py file_cap 과 같다). */
export function fileCap(name) {
  if (FILE_CAPS[name] !== undefined) return FILE_CAPS[name];
  for (const [prefix, cap] of Object.entries(PREFIX_CAPS))
    if (name.startsWith(prefix)) return cap;
  return undefined;
}

function keyPaths(v, pred, path = "$") {
  const out = [];
  if (Array.isArray(v))
    v.forEach((x, i) => out.push(...keyPaths(x, pred, `${path}[${i}]`)));
  else if (v && typeof v === "object") {
    for (const [k, x] of Object.entries(v)) {
      const p = `${path}.${k}`;
      if (pred(k, x)) out.push(p);
      out.push(...keyPaths(x, pred, p));
    }
  }
  return out;
}

/** P7 공개 diag/ 에 없어야 하는 로그인 판정 키(docs/p7_design.md §7 — gex 로 시작하는 키도) */
const DIAG_LOGIN_KEYS = new Set(["fear_greed", "verdict"]);

/** P7 diag/ 내용 검사 — 로그인 판정 키 0(manifest.py diag_problems 와 같다). */
export function diagProblems(name, parsed) {
  if (!name.startsWith("diag/")) return [];
  return keyPaths(
    parsed,
    (k) => DIAG_LOGIN_KEYS.has(k) || k.startsWith("gex"),
  ).map((p) => `${name}: 로그인 판정 키(${p})`);
}

/** P6 trade/ 내용 검사 — origin local 0(manifest.py trade_problems 와 같다). 시세 칸은 priceProblems. */
export function tradeProblems(name, parsed) {
  if (!name.startsWith("trade/")) return [];
  return keyPaths(parsed, (k, x) => k === "origin" && x === "local").map(
    (p) => `${name}: 로그인 전용 매핑(origin local — ${p})`,
  );
}

function priceShapeOk(kind, x) {
  if (x === null || x === undefined) return true;
  if (kind === "hhmm") return typeof x === "string" && /^\d{2}:\d{2}$/.test(x);
  return typeof x === "number";
}

/** P9 원시세 칸 검사 — PRICE_KEYS 키 0, 예외는 PRICE_FIELD_ALLOW 의 (파일·키)이고 값 모양까지(manifest.py price_problems). */
export function priceProblems(name, parsed) {
  const allow = new Map(
    PRICE_FIELD_ALLOW.map(([f, k, kind]) => [`${f}\u0000${k}`, kind]),
  );
  const out = [];
  for (const p of keyPaths(parsed, (k) => PRICE_KEYS.has(k))) {
    const key = p.slice(p.lastIndexOf(".") + 1);
    if (!allow.has(`${name}\u0000${key}`)) out.push(`${name}: 시세 칸(${p})`);
  }
  for (const [f, k, kind] of PRICE_FIELD_ALLOW) {
    if (f !== name) continue;
    for (const p of keyPaths(
      parsed,
      (kk, x) => kk === k && !priceShapeOk(kind, x),
    )) {
      out.push(`${name}: 시세 칸 예외의 값 모양이 다르다(${p} — ${kind})`);
    }
  }
  return out;
}

function sizeProblem(name, bytes) {
  const cap = fileCap(name);
  return cap !== undefined && bytes > cap
    ? [`${name}: 크기 상한을 넘는다`]
    : [];
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
  if (!env || typeof env !== "object" || Array.isArray(env))
    return { errors: [`${p}: 봉투가 아님`], claim: null };
  if (typeof env.source !== "string" || !env.source)
    errors.push(`${p}: source 없음`);
  if (typeof env.as_of !== "string" || !AWARE_ISO.test(env.as_of))
    errors.push(`${p}: as_of 없음·시간대 없음`);
  if (!QUALITIES.includes(env.quality))
    errors.push(`${p}: quality 없음·모르는 값`);
  if (!("data" in env)) errors.push(`${p}: data 없음`);
  const bad = loginSourcePaths(env);
  if (bad.length)
    errors.push(
      `${p}: source 에 로그인 등급 출처 이름(${bad.slice(0, 3).join(", ")})`,
    );
  const dartDir = DART_SUBDIRS.includes(p.split("/")[0]);
  errors.push(
    ...(dartDir ? dartOnlyProblems(p, text, env) : traceProblems(p, text, env)),
  );
  errors.push(...tradeProblems(p, env));
  errors.push(...diagProblems(p, env));
  errors.push(...priceProblems(p, env));
  const claim =
    env.data && typeof env.data === "object" ? env.data.xlsx_sha256 : undefined;
  return { errors, claim: typeof claim === "string" ? claim : null };
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
    errors.push(...sizeProblem(p, body.length));
    if (p.endsWith(".xlsx")) continue;
    const r = checkSubdirJson(p, files[p]);
    errors.push(...r.errors);
    if (p.startsWith("reports/"))
      claims.set(p.replace(/\.json$/, ".xlsx"), r.claim);
  }
  const sha = new Map(hashes);
  for (const p of ok) {
    if (!p.endsWith(".xlsx")) continue;
    const claim = claims.get(p);
    if (!claim) errors.push(`${p}: 짝 JSON 의 xlsx_sha256 이 없음`);
    else if (claim !== sha.get(p))
      errors.push(`${p}: 짝 JSON 의 xlsx_sha256 과 다름`);
  }
  for (const [xl, claim] of claims)
    if (claim && !sha.has(xl))
      errors.push(`${xl}: 짝 JSON 은 엑셀이 있다는데 파일이 없음`);
  if (entry.count !== ok.length) errors.push(`${name}/: manifest 개수와 다름`);
  if (entry.bytes !== bytes) errors.push(`${name}/: manifest 합계 크기와 다름`);
  if (entry.digest !== dirDigest(hashes))
    errors.push(`${name}/: manifest 요약 해시와 다름`);
  if (DIR_CAPS[name] !== undefined && bytes > DIR_CAPS[name])
    errors.push(`${name}/: 합계 크기 상한을 넘는다`);
  if (DART_SUBDIRS.includes(name)) {
    if (entry.source !== "DART")
      errors.push(`${name}/: source 가 DART 가 아님`);
  } else if (typeof entry.source !== "string" || !entry.source)
    errors.push(`${name}/: source 없음`);
  else if (LOGIN_SOURCES.test(entry.source))
    errors.push(`${name}/: 로그인 등급 출처 이름이 source 에 있음`);
  if (typeof entry.as_of !== "string" || !AWARE_ISO.test(entry.as_of))
    errors.push(`${name}/: as_of 없음·시간대 없음`);
  if (!QUALITIES.includes(entry.quality))
    errors.push(`${name}/: quality 없음·모르는 값`);
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
    return {
      copy,
      errors: [String(e instanceof Error ? e.message : e)],
      ignored: [],
    };
  }
  for (const e of entries) {
    const name = fileName(e.name);
    if (!NAME_RE.test(name) || name === "manifest.json") {
      errors.push(`${e.name || "(이름 없음)"}: 허용하지 않는 이름`);
      continue;
    }
    if (typeof e.source !== "string" || !e.source)
      errors.push(`${name}: source 없음`);
    else if (LOGIN_SOURCES.test(e.source))
      errors.push(`${name}: 로그인 등급 출처 이름이 source 에 있음`);
    if (typeof e.as_of !== "string" || !AWARE_ISO.test(e.as_of))
      errors.push(`${name}: as_of 없음·시간대 없음`);
    if (!QUALITIES.includes(e.quality))
      errors.push(`${name}: quality 없음·모르는 값`);
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
    if (bad.length)
      errors.push(
        `${name}: source 에 로그인 등급 출처 이름(${bad.slice(0, 3).join(", ")})`,
      );
    if (DART_ONLY.has(name))
      errors.push(...dartOnlyProblems(name, text, parsed));
    else if (P5_FLAT_NAMES.has(name))
      errors.push(...traceProblems(name, text, parsed));
    errors.push(...priceProblems(name, parsed));
    errors.push(...sizeProblem(name, bytesOf(files[name]).length));
    copy.push(name);
  }
  const seen = new Set();
  for (const d of dirs) {
    if (!SUBDIRS.includes(d.name) || seen.has(d.name)) {
      errors.push(
        `${d.name || "(이름 없음)"}/: 허용하지 않는(또는 겹친) 하위 폴더`,
      );
      continue;
    }
    seen.add(d.name);
    const plan = planDir(d, files);
    errors.push(...plan.errors);
    copy.push(...plan.copy);
  }
  const listed = new Set([...copy, "manifest.json"]);
  const ignored = Object.keys(files).filter((n) => !listed.has(n));
  return {
    copy: errors.length ? [] : [...copy, "manifest.json"],
    errors,
    ignored,
  };
}

/**
 * 데이터 디렉터리 → { 경로: 글자(.json)·바이트(그 밖) } — 평평한 *.json + 하위 폴더(SUBDIRS)의 파일.
 * @param {string} srcAbs
 */
export function readDataDir(srcAbs) {
  const files = {};
  for (const n of readdirSync(srcAbs)) {
    const p = join(srcAbs, n);
    if (n.endsWith(".json") && statSync(p).isFile())
      files[n] = readFileSync(p, "utf8");
  }
  // 하위 폴더는 아래까지(P6 trade/hs/<장>.json 처럼 한 단계 더) — 허용 이름 검사는 planDir 이 한다
  const walk = (dir, rel) => {
    for (const n of readdirSync(dir)) {
      const p = join(dir, n);
      const r = `${rel}/${n}`;
      if (statSync(p).isDirectory()) walk(p, r);
      else
        files[r] = n.endsWith(".json")
          ? readFileSync(p, "utf8")
          : readFileSync(p);
    }
  };
  for (const sub of SUBDIRS) {
    const dir = join(srcAbs, sub);
    if (!existsSync(dir) || !statSync(dir).isDirectory()) continue;
    walk(dir, sub);
  }
  return files;
}

function main(argv) {
  const [src, dist = "dist-public"] = argv;
  if (!src) {
    console.error(
      "사용법: merge-public-data.mjs <public-data 디렉터리> [dist-public]",
    );
    return 2;
  }
  const srcAbs = resolve(src);
  const distAbs = resolve(dist);
  if (!existsSync(join(srcAbs, "manifest.json"))) {
    console.error(`${src}: manifest.json 이 없다`);
    return 2;
  }
  if (!existsSync(join(distAbs, "index.html"))) {
    console.error(`${dist}: 공개 빌드가 없다 — 먼저 npm run build:public`);
    return 2;
  }
  const files = readDataDir(srcAbs);
  let manifest;
  try {
    manifest = JSON.parse(files["manifest.json"]);
  } catch {
    console.error("manifest.json 이 JSON 이 아니다");
    return 1;
  }
  const plan = planMerge(manifest, files);
  for (const e of plan.errors) console.error(`실패: ${e}`);
  for (const n of plan.ignored) console.warn(`건너뜀(manifest 에 없음): ${n}`);
  if (plan.errors.length) return 1;
  const out = join(distAbs, "data");
  for (const n of plan.copy) {
    const dest = join(out, n);
    mkdirSync(dirname(dest), { recursive: true });
    copyFileSync(join(srcAbs, n), dest);
  }
  const sub = plan.copy.filter((n) => n.includes("/")).length;
  console.log(`복사 ${plan.copy.length}개(하위 폴더 ${sub}개) → ${dist}/data/`);
  return 0;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  process.exit(main(process.argv.slice(2)));
}
