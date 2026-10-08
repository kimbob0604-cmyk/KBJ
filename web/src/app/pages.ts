// 페이지 등록부 — 13개 페이지의 번호·제목·단계·등급(docs/PLAN.md §4·§8, DATA_TIERS §2, p3_design §6.5).
//
// P3 에 위젯을 채우는 페이지는 1·2·4·10(W2). 나머지는 빈 자리 패널 1개 + '준비 중(Pn)' + 등급 자물쇠(§6.5).
// 위젯 모듈은 src/pages/p<번호>_<이름>.ts — 아래 glob 이 빌드 때 찾는다. 파일이 아직 없으면 빈 자리로 그린다.
import type { PageModule } from './types';

export type PageTier = 'public' | 'login' | 'mixed';

export interface PageDef {
  n: number;
  title: string;
  /** 이 페이지를 채우는 단계 */
  phase: 'P3' | 'P4' | 'P5' | 'P6' | 'P7' | 'P8';
  /**
   * 공개판에서 보이는 정도(DATA_TIERS §2): public = 빈 자리도 공개 표시, login = 자물쇠,
   * mixed = 공개 위젯과 로그인 위젯이 섞임(페이지 모듈이 패널마다 정한다)
   */
  tier: PageTier;
  /** 그 단계에서 들어올 것(빈 자리 안내 문구) */
  plan: string;
  /** 공개판 자물쇠 사유(ui/lock.ts LOCK_REASONS 키 또는 문구) — tier 가 login 일 때 */
  lock?: string;
}

export const PAGES: readonly PageDef[] = [
  { n: 1, title: '시장', phase: 'P3', tier: 'mixed', plan: '지수·시장 거래대금·업종 히트맵·시장폭·투자자 수급' },
  { n: 2, title: '신고가 보드', phase: 'P3', tier: 'login', lock: 'board', plan: '60일·52주·역사적 신고가, 섹터·테마 집계, 랭킹, 탐지' },
  { n: 3, title: '진단', phase: 'P7', tier: 'login', lock: 'options', plan: '종합 판정·공포탐욕·GEX 레벨(Flip·월·기대변동폭)' },
  { n: 4, title: '수급·스크리닝', phase: 'P3', tier: 'login', lock: 'investor', plan: '스크리너 6기준·투자자별·기관 7구분·종목 상세·장중 잠정' },
  { n: 5, title: '공시·일정', phase: 'P4', tier: 'public', plan: 'DART 공시 피드·잠정실적·경제 캘린더·오버행' },
  { n: 6, title: '매크로', phase: 'P5', tier: 'login', lock: '저작권·작성기관 제한 시리즈 — 공개 지표는 P5 에 공개판으로', plan: 'BOK 보고서 지표·ECOS·FRED(정부 시리즈)' },
  { n: 7, title: '수출입', phase: 'P6', tier: 'public', plan: '관세청 월간·10일·20일 속보, 품목·국가·지역 탐색' },
  { n: 8, title: '종목 상세', phase: 'P4', tier: 'login', lock: 'kis', plan: '재무·부문 매출·컨센서스·밸류에이션 밴드·수급' },
  { n: 9, title: '테마·밸류체인', phase: 'P5', tier: 'login', lock: 'quote_based', plan: '테마·체인 모니터, 밸류체인 맵, 테마 강도' },
  { n: 10, title: 'ETF 수급', phase: 'P3', tier: 'login', lock: 'etf', plan: '순유입·투자자별·유형별·괴리율·구성종목 변동' },
  { n: 11, title: '알림·규칙', phase: 'P8', tier: 'login', lock: 'personal', plan: '규칙 기반 기술적 알림·발송 이력·알림 후 성과' },
  { n: 12, title: '백테스트·저널', phase: 'P8', tier: 'login', lock: 'personal', plan: '규칙·신호 백테스트, 분석 일지, 포트폴리오 저널' },
  // 13 운영: PLAN §8 에 단계가 적혀 있지 않다 — P8 로 둔다 [확인 필요]
  { n: 13, title: '운영', phase: 'P8', tier: 'login', lock: 'personal', plan: '데이터 신선도·작업 모니터·헬스·발송 기록' },
];

/** 이번 단계(P3)에 위젯이 들어오는 페이지 */
export const P3_PAGES: readonly number[] = PAGES.filter((p) => p.phase === 'P3').map((p) => p.n);

export type PageLoader = () => Promise<PageModule>;

/**
 * 위젯 모듈 찾기 — `src/pages/p1_market.ts` 같은 파일. 빌드 때 정해지므로 없는 파일은 빈 목록이다.
 * 페이지마다 따로 불러온다(처음 보일 때).
 */
const FOUND = import.meta.glob<PageModule>('../pages/p{1,2,4,10}_*.ts', { import: 'default' });

export function pageLoaders(found: Record<string, () => Promise<PageModule>> = FOUND): Map<number, PageLoader> {
  const out = new Map<number, PageLoader>();
  for (const [path, load] of Object.entries(found)) {
    const m = /\/p(\d+)_[a-z0-9_]+\.ts$/.exec(path);
    if (m?.[1]) out.set(Number(m[1]), load);
  }
  return out;
}

export function pageDef(n: number): PageDef | undefined {
  return PAGES.find((p) => p.n === n);
}
