// 페이지 8 종목 상세 '담은 ETF' 모듈(docs/p5_design.md §4.4·§6 — 로그인). 이 종목을 담은 ETF(펀드마다 마지막 PDF
// 스냅 — 비중 상위·액티브 여부). 운용사 PDF 기준일이 다르면 그대로 보인다.
//
// 고리: p8_stock.ts '개요' 탭 모듈 목록(WRAP — 설계 §1.8 4). 공개 빌드는 이 모듈을 싣지 않고 같은 제목 자물쇠만.
// 공개판은 자물쇠만, 요청 없음.
import { fill } from '../p1_market';
import { drawHolders, type Holders } from '../p10/holdings';
import { mkPanel, tabGrid, type TabCtx } from './common';

export const TITLE = '담은 ETF';
export const HINT = 'etf.collect 08:00';

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: TITLE, tier: 'login', span: 12, lock: 'etf_issuers' });
  tabGrid(el, p);
  if (p.locked) return;
  p.setDef('운용사 공시 PDF 마지막 기준일의 비중·수량. 액티브 ETF 는 꼬리표. 추적하는 펀드만 센다.');
  await fill(
    p,
    () => t.get<Holders>(`etf/holders/${t.code}`),
    (env) => drawHolders(p, env),
    HINT,
  );
}
