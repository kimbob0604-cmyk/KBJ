// 자물쇠(공개 빌드에서 로그인 등급 위젯 자리) — 사유 문구는 미리보기 .lockmsg 와 같은 뜻(§6.6).
// 자물쇠 패널에는 데이터를 그리지 않는다(흐리게 감추는 게 아니라 아예 요청·렌더를 하지 않는다).
import { h } from './dom';

export const LOCK_REASONS = {
  kis: 'KIS 시세는 제3자 제공 금지라 로그인 후 표시',
  krx: 'KRX 원시세 재배포 금지 — 공개판은 TradingView 위젯으로 대체',
  krx_daily: 'KRX 일별 시세·거래대금 — 재배포 금지',
  quote_based: '시세 기반 집계 — 원천이 KRX 계열',
  board: '시세 원천이 모두 KRX 계열',
  investor: '투자자별 매매는 KRX·KIS 원천',
  etf: 'KRX ETF 시세·투자자별',
  etf_issuers: '운용사 구성종목 — 약관마다 달라 로그인',
  options: 'KIS 옵션 체인 기반',
  consensus: '컨센서스 — 유료·재배포 금지',
  personal: '개인 기능 — 로그인',
} as const;

export type LockReason = keyof typeof LOCK_REASONS;
/** 사유 키 또는 직접 쓴 문구 */
export type LockText = LockReason | (string & Record<never, never>);

export function lockText(reason: LockText): string {
  return reason in LOCK_REASONS ? LOCK_REASONS[reason as LockReason] : reason;
}

/** 자물쇠 상자(패널 본문 자리). */
export function lockBox(reason: LockText): HTMLDivElement {
  return h('div', { class: 'lockmsg' }, h('div', null, h('b', null, 'LOGIN'), lockText(reason)));
}
