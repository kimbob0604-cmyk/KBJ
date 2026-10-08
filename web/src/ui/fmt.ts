// 숫자·시각 표시 — 화면에서만 반올림한다(docs/p3_design.md §6.8 fmt.test).
//
// 규칙
// - API·공개 JSON 의 금액은 **원 단위 정수**다. 억·조 변환과 반올림은 여기서만 한다(계산에 되먹이지 않는다).
// - 값이 없으면(null·undefined·NaN·Infinity) '—' — 0 으로 그리지 않는다(절대 규칙 2: 추정을 단정하지 않는다).
// - 부호: 양수 '+', 음수 '−' 대신 ASCII '-'(표 정렬·복사 일관). 반올림해 0 이 되면 부호를 붙이지 않는다(-0 금지).
// - 시각: 입력은 ISO 8601(UTC·오프셋 포함) 또는 Date. 표시는 늘 KST(UTC+9, 서머타임 없음)이고 브라우저 시간대와 무관하다.
//   로케일(ICU) 차이로 결과가 바뀌지 않게 자릿수 묶음·날짜 조립을 직접 한다.

export const DASH = '—';
export const WON_PER_EOK = 100_000_000;
export const WON_PER_JO = 1_000_000_000_000;
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

export type Num = number | null | undefined;

function ok(v: Num): v is number {
  return typeof v === 'number' && Number.isFinite(v);
}

/** 정수부 세 자리 묶음. */
function group(intDigits: string): string {
  return intDigits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

/** 고정 소수 자릿수 + 세 자리 묶음. 부호는 붙이지 않고 절댓값 기준. */
function fixedAbs(v: number, digits: number): string {
  const s = Math.abs(v).toFixed(digits);
  const [i = '0', f] = s.split('.');
  return f === undefined ? group(i) : `${group(i)}.${f}`;
}

function isZeroAfterRounding(v: number, digits: number): boolean {
  return Number(Math.abs(v).toFixed(digits)) === 0;
}

/** 숫자 표시. signed=true 면 양수에 '+'. */
export function num(v: Num, digits = 0, signed = false): string {
  if (!ok(v)) return DASH;
  if (isZeroAfterRounding(v, digits)) return fixedAbs(0, digits);
  const sign = v < 0 ? '-' : signed ? '+' : '';
  return sign + fixedAbs(v, digits);
}

/** 퍼센트. 기본 소수 2자리, 부호 있음: +1.23% */
export function pct(v: Num, digits = 2, signed = true): string {
  return ok(v) ? `${num(v, digits, signed)}%` : DASH;
}

/** 배수: 1.23x */
export function times(v: Num, digits = 2): string {
  return ok(v) ? `${num(v, digits)}x` : DASH;
}

/** 원 → 억(정수 반올림): 1,234억 */
export function eok(won: Num, digits = 0, signed = false): string {
  return ok(won) ? `${num(won / WON_PER_EOK, digits, signed)}억` : DASH;
}

/** 원 → 억 숫자만(단위 없이 — 표 머리에 '억'이 있을 때). */
export function eokNum(won: Num, digits = 0, signed = false): string {
  return ok(won) ? num(won / WON_PER_EOK, digits, signed) : DASH;
}

/** 원 → 1조 이상은 '12.3조', 그 아래는 '1,234억'. */
export function joEok(won: Num, signed = false): string {
  if (!ok(won)) return DASH;
  // 억으로 반올림해 1만억(=1조)이 되는 값(9,999.5억 이상)도 조로 — '10,000억' 이 나오지 않게
  if (Math.abs(won) >= WON_PER_JO || Math.round(Math.abs(won) / WON_PER_EOK) >= 10_000) {
    return `${num(won / WON_PER_JO, 1, signed)}조`;
  }
  return eok(won, 0, signed);
}

/** 값의 부호에 맞는 색 클래스(국내 관례: 상승 적색 up, 하락 청색 down). */
export function signClass(v: Num): 'up' | 'down' | 'dim' {
  if (!ok(v) || v === 0) return 'dim';
  return v > 0 ? 'up' : 'down';
}

// ── KST 시각 ─────────────────────────────────────────────────

export interface KstParts {
  year: number;
  month: number; // 1~12
  day: number;
  hour: number;
  minute: number;
  second: number;
  /** 0=일 ~ 6=토 */
  weekday: number;
}

/** ISO 문자열·Date 를 Date 로. 시간대 없는 ISO(naive)는 거부한다 — 뜻이 모호하다. */
export function toDate(t: string | Date): Date | null {
  if (t instanceof Date) return Number.isNaN(t.getTime()) ? null : t;
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(t) && t.includes('T')) return null;
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function kstParts(t: string | Date): KstParts | null {
  const d = toDate(t);
  if (!d) return null;
  const k = new Date(d.getTime() + KST_OFFSET_MS);
  return {
    year: k.getUTCFullYear(),
    month: k.getUTCMonth() + 1,
    day: k.getUTCDate(),
    hour: k.getUTCHours(),
    minute: k.getUTCMinutes(),
    second: k.getUTCSeconds(),
    weekday: k.getUTCDay(),
  };
}

const p2 = (n: number): string => String(n).padStart(2, '0');

/** 'HH:MM' (KST) */
export function kstTime(t: string | Date | null | undefined): string {
  const p = t ? kstParts(t) : null;
  return p ? `${p2(p.hour)}:${p2(p.minute)}` : DASH;
}

/** 'HH:MM:SS' (KST) */
export function kstClock(t: string | Date | null | undefined): string {
  const p = t ? kstParts(t) : null;
  return p ? `${p2(p.hour)}:${p2(p.minute)}:${p2(p.second)}` : DASH;
}

/** 'MM/DD HH:MM' (KST) */
export function kstStamp(t: string | Date | null | undefined): string {
  const p = t ? kstParts(t) : null;
  return p ? `${p2(p.month)}/${p2(p.day)} ${p2(p.hour)}:${p2(p.minute)}` : DASH;
}

/** 'YYYY-MM-DD' (KST 날짜). 날짜만 있는 'YYYY-MM-DD' 입력은 그대로 돌려준다(시간대 변환 없음). */
export function kstDate(t: string | Date | null | undefined): string {
  if (typeof t === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(t)) return t;
  const p = t ? kstParts(t) : null;
  return p ? `${p.year}-${p2(p.month)}-${p2(p.day)}` : DASH;
}

/** 'MM/DD' — 날짜('YYYY-MM-DD') 또는 시각 입력. */
export function shortDate(t: string | Date | null | undefined): string {
  const d = kstDate(t);
  return d === DASH ? DASH : `${d.slice(5, 7)}/${d.slice(8, 10)}`;
}
