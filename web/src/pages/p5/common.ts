// 페이지 5(공시·일정 — DART 패널) 공통 — 위젯 문맥·날짜 셈(docs/p4_design.md §6.4).
//
// 로그인은 'filings/<이름>' API(쿼리로 거른다), 공개는 public.export 파일(filings_feed·earnings_prelim·overhang_recent·
// insider_recent·earnings_schedule — 같은 행 모양, 거르기는 화면에서). 행의 종목 → 페이지 8(openStock).
import type { DataSource, Tier } from '../../data/source';
import { kstDate } from '../../ui/fmt';
import type { Panel } from '../../ui/panel';

export interface P5Ctx {
  readonly tier: Tier;
  readonly source: DataSource;
  readonly now: () => Date;
  /** 종목 상세(페이지 8)로 */
  readonly open: (code: string) => void;
}

export interface Widget {
  readonly panel: Panel;
  /** 불러와 그리기(필터를 바꿀 때·주기 새로 고침) */
  load(): Promise<void>;
}

/** 'YYYY-MM-DD' 에 n 일 더하기(달력일) */
export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number);
  const t = new Date(Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1) + n * 86_400_000);
  return t.toISOString().slice(0, 10);
}

/** 오늘(KST 달력일) */
export function today(now: () => Date): string {
  return kstDate(now());
}

/** DART 법인 구분 → 시장(Y 코스피·K 코스닥·N 코넥스, 그 밖은 null) */
export function marketOfCls(cls: string | null | undefined): string | null {
  return cls === 'Y' ? 'KOSPI' : cls === 'K' ? 'KOSDAQ' : cls === 'N' ? 'KONEX' : null;
}

/** 절댓값 비율 하한 */
export function passMinRatio(ratio: number | null | undefined, min: number | null): boolean {
  if (min === null) return true;
  return typeof ratio === 'number' && Number.isFinite(ratio) && Math.abs(ratio) >= min;
}
