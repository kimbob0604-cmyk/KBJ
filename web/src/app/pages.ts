// 페이지 등록부 — 13개 페이지의 번호·제목·단계·등급(docs/PLAN.md §4·§8, DATA_TIERS §2, p3_design §6.5, p4_design §1.2).
//
// P3 에 위젯을 채우는 페이지는 1·2·4·10, P4 는 5(공시·일정의 DART 패널)·8(종목 상세), P5 는 6(매크로) — p5_design §6,
// P6 은 7(수출입 — p6_design §6, 진입 p7_trade.ts 탭 틀은 FND).
// 9(테마·밸류체인)는 사전 통합을 사용자가 따로 진행해 P5 에서 뺐다 — P3 자리표시 그대로. 나머지는 빈 자리 패널 1개 + '준비 중(Pn)' + 등급 자물쇠(§6.5). 위젯 모듈은 src/pages/p<번호>_<이름>.ts
// (진입 파일 하나 — 하위 폴더 src/pages/p8/** 는 진입 파일이 import 한다) — 아래 glob 이 빌드 때 찾는다. 파일이 아직
// 없으면 빈 자리로 그린다. P5 의 6 진입 파일은 MAC 이 채웠다.
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
  { n: 2, title: '신고가 보드', phase: 'P3', tier: 'login', lock: 'board', plan: '역사적·52주·120일 신고가, 섹터·테마 집계, 랭킹, 탐지' },
  {
    n: 3,
    title: '진단',
    phase: 'P7',
    tier: 'mixed',
    plan: '종합 판정·공포탐욕·시장 진단·GEX 레벨(Flip·월·기대변동폭) — 공공지표 진단·이벤트는 공개',
  },
  { n: 4, title: '수급·스크리닝', phase: 'P3', tier: 'login', lock: 'investor', plan: '스크리너 6기준·투자자별·기관 7구분·종목 상세·장중 잠정' },
  // 5: DART 패널은 공개(관심종목만 보기 토글은 로그인), 경제 캘린더·뉴스는 P5 — 패널마다 등급이 달라 mixed(D-P4-15)
  { n: 5, title: '공시·일정', phase: 'P4', tier: 'mixed', plan: 'DART 공시 피드·잠정실적·오버행·내부자·실적 일정 (경제 캘린더·뉴스는 P5)' },
  // 6: 공개 = ECOS·KOSIS·FRED 정부 시리즈·경제 캘린더, 로그인 = '시장' 탭(저작권·작성기관 제한 시리즈) — D-P5-5·6
  {
    n: 6,
    title: '매크로',
    phase: 'P5',
    tier: 'mixed',
    plan: '보고서 지표·ECOS·KOSIS·FRED(정부 시리즈)·경제 캘린더(공개) / 시장 탭 — 저작권·작성기관 제한 시리즈(로그인)',
  },
  // 7: 관세청 공개 자료 전부(DATA_TIERS §1) — 탭 6(속보·품목·국가·지역·급등·104품목) + 매핑 종목 랭킹(docs/p6_design.md §6)
  {
    n: 7,
    title: '수출입',
    phase: 'P6',
    tier: 'public',
    plan: '관세청 10일 속보(1~10·1~20·월)·품목·국가·지역 탐색·급등·104품목·매핑 종목 랭킹',
  },
  // 8: 공개 = DART 재무·리포트·공시·오버행·내부자(주식 수)·TradingView, 로그인 = 시세·수급·컨센서스·밸류에이션·
  //    시나리오 적정가(구 TAM — 화면에 'TAM' 을 쓰지 않는다, D-P4-12)·금액(D-P4-15)
  {
    n: 8,
    title: '종목 상세',
    phase: 'P4',
    tier: 'mixed',
    plan: '재무·dart-report·공시·오버행·내부자(공개) / 시세·수급·컨센서스·밸류에이션 밴드·시나리오 적정가(로그인)',
  },
  // 9: 테마·밸류체인 사전 통합은 사용자가 따로 진행(2026-10-13 결정 Q1) — P5 에서 제외, P3 자리표시 그대로
  { n: 9, title: '테마·밸류체인', phase: 'P5', tier: 'login', lock: 'quote_based', plan: '테마·체인 모니터, 밸류체인 맵, 테마 강도' },
  {
    n: 10,
    title: 'ETF 수급',
    phase: 'P3',
    tier: 'login',
    lock: 'etf',
    plan: '순유입·투자자별·유형별·괴리율·구성종목 변동 (ETF 맵·보유 탐색·액티브 동시 편입은 P5)',
  },
  { n: 11, title: '알림·규칙', phase: 'P8', tier: 'login', lock: 'personal', plan: '규칙 기반 기술적 알림·발송 이력·알림 후 성과' },
  { n: 12, title: '백테스트·저널', phase: 'P8', tier: 'login', lock: 'personal', plan: '규칙·신호 백테스트, 분석 일지, 포트폴리오 저널' },
  // 13 운영: PLAN §8 에 단계가 적혀 있지 않다 — P8 로 둔다 [확인 필요]
  { n: 13, title: '운영', phase: 'P8', tier: 'login', lock: 'personal', plan: '데이터 신선도·작업 모니터·헬스·발송 기록' },
];

/** P3 에 위젯이 들어온 페이지 */
export const P3_PAGES: readonly number[] = PAGES.filter((p) => p.phase === 'P3').map((p) => p.n);

/** P4 에 위젯이 들어오는 페이지(5·8) */
export const P4_PAGES: readonly number[] = PAGES.filter((p) => p.phase === 'P4').map((p) => p.n);

/** P5 에 위젯이 들어오는 페이지(6). 9 는 단계 표기만 P5 이고 위젯이 없다(테마 사전 통합은 사용자가 따로 — 자리표시) */
export const P5_PAGES: readonly number[] = PAGES.filter((p) => p.phase === 'P5' && p.n !== 9).map((p) => p.n);

/** P6 에 위젯이 들어오는 페이지(7 — 페이지 8 겹쳐 보기 패널은 P4 페이지 안 고리, WRAP) */
export const P6_PAGES: readonly number[] = PAGES.filter((p) => p.phase === 'P6').map((p) => p.n);

/** P7 위젯 페이지 — 3 진단(docs/p7_design.md §6, mixed: 공공지표 진단·이벤트만 공개) */
export const P7_PAGES: readonly number[] = PAGES.filter((p) => p.phase === 'P7').map((p) => p.n);

export type PageLoader = () => Promise<PageModule>;

/**
 * 위젯 모듈 찾기 — `src/pages/p1_market.ts` 같은 파일. 빌드 때 정해지므로 없는 파일은 빈 목록이다.
 * 페이지마다 따로 불러온다(처음 보일 때). 번호는 위젯이 있는 단계의 페이지만(P3 1·2·4·10 + P4 5·8 + P5 6 + P6 7) — 하위
 * 폴더(`src/pages/p8/*.ts`)는 이 glob 에 걸리지 않는다(진입 파일이 import).
 */
const FOUND = import.meta.glob<PageModule>('../pages/p{1,2,3,4,5,6,7,8,10}_*.ts', { import: 'default' });

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
