// CSV 내보내기(docs/p6_design.md D-P6-19 — 페이지 7 패널마다 CSV 버튼). 부품은 둘: 글자 만들기(toCsv)와 내려받기(downloadCsv).
//
// - 엑셀이 한글을 깨지 않게 UTF-8 BOM(U+FEFF)을 앞에 붙이고 줄은 CRLF. RFC 4180 이스케이프: 쉼표·큰따옴표·줄바꿈이 든 칸은
//   큰따옴표로 감싸고 안의 큰따옴표는 두 번.
// - 수식 주입 막기: 문자열 칸이 = + - @ 탭 CR 로 시작하면 앞에 작은따옴표(')를 붙인다(수 칸은 그대로 — 음수 보존).
// - 값이 없으면(null·undefined·NaN·무한) 빈 칸 — 0 으로 채우지 않는다(절대 규칙 2). 반올림은 하지 않는다(format 이 정한다).
// - 원천 줄: 표 아래에 '출처,<원천>' 한 줄을 붙일 수 있다(meta) — 관세청 자료 출처 표시.

export interface CsvColumn<R> {
  /** 머리 글(한글 가능) */
  label: string;
  /** 칸 값 — 행의 키 이름 또는 함수 */
  value: keyof R | ((row: R) => unknown);
}

export interface CsvOptions {
  /** 표 아래 메타 줄(예 [['출처', '관세청(공공데이터포털)'], ['기준', '2026-09']]) */
  meta?: readonly (readonly [string, string])[];
}

export const BOM = '﻿';
const EOL = '\r\n';
const FORMULA = /^[=+\-@\t\r]/;

function cell(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : '';
  if (typeof v === 'bigint' || typeof v === 'boolean') return String(v);
  let s = typeof v === 'string' ? v : JSON.stringify(v);
  if (FORMULA.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** 행 → CSV 글자(BOM 포함) */
export function toCsv<R>(rows: readonly R[], cols: readonly CsvColumn<R>[], opts: CsvOptions = {}): string {
  const lines = [cols.map((c) => cell(c.label)).join(',')];
  for (const r of rows) {
    lines.push(cols.map((c) => cell(typeof c.value === 'function' ? c.value(r) : r[c.value])).join(','));
  }
  if (opts.meta?.length) {
    lines.push('');
    for (const [k, v] of opts.meta) lines.push(`${cell(k)},${cell(v)}`);
  }
  return BOM + lines.join(EOL) + EOL;
}

/** 파일 이름 — 소문자·숫자·밑줄·하이픈·점만(그 밖은 밑줄), .csv 로 끝나게 */
export function csvName(name: string): string {
  const base = name.replace(/\.csv$/i, '').replace(/[^0-9A-Za-z_.-]+/g, '_').replace(/^[._]+/, '') || 'data';
  return `${base.slice(0, 80)}.csv`;
}

export interface DownloadDeps {
  /** 시험 주입 — 기본은 document·URL */
  doc?: Document;
  createUrl?: (b: Blob) => string;
  revokeUrl?: (u: string) => void;
}

/** CSV 글자를 파일로 내려받게 한다(요청 없음 — 브라우저 안에서 Blob). 만든 파일 이름을 돌려준다. */
export function downloadCsv(name: string, text: string, deps: DownloadDeps = {}): string {
  const doc = deps.doc ?? document;
  const createUrl = deps.createUrl ?? ((b: Blob) => URL.createObjectURL(b));
  const revokeUrl = deps.revokeUrl ?? ((u: string) => URL.revokeObjectURL(u));
  const file = csvName(name);
  const url = createUrl(new Blob([text], { type: 'text/csv;charset=utf-8' }));
  const a = doc.createElement('a');
  a.href = url;
  a.download = file;
  a.rel = 'noopener';
  a.style.display = 'none';
  doc.body.append(a);
  try {
    a.click();
  } finally {
    a.remove();
    revokeUrl(url);
  }
  return file;
}
