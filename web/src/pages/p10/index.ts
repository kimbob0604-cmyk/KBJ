// 페이지 10 P5 패널(docs/p5_design.md §6 — ETF 맵·보유 탐색·액티브 동시 편입, 전부 로그인). p10_etf.ts 가 P3 패널
// 뒤에 붙인다(mountP5). 공개판은 자물쇠만, 요청 없음.
//
// - 보유 탐색은 처음에 요청하지 않는다(코드를 넣거나 맵 칸을 누를 때만).
// - 주기 새로 고침은 맵·동시 편입만(보유 탐색은 사용자가 고른 것을 다시 부른다).
import type { PageContext } from '../../app/types';
import { h } from '../../ui/dom';
import { type Panel, panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { fill, filters } from '../p1_market';
import { type Consensus, DAYS, type Days, drawConsensus } from './consensus';
import { drawHolders, drawHoldings, type Holders, type Holdings, MODES as HOLD_MODES, type Mode, normalizeCode } from './holdings';
import { drawMap, GROUPS, LEV, type MapData, type MapGroup, type MapPeriod, PERIODS } from './map';

export const TITLES = { map: 'ETF 맵', hold: '보유 탐색', cons: '액티브 동시 편입' } as const;
export const HINT_ETF = 'etf.collect 08:00 · krx.daily 08:05';
export const HINT_HOLD = 'etf.collect 08:00';

export interface P5Panels {
  map: Panel;
  hold: Panel;
  cons: Panel;
  /** 주기 새로 고침(맵·동시 편입) */
  refresh(): Promise<void>;
  /** 첫 불러오기 */
  ready: Promise<void>;
}

export function mountP5(ctx: PageContext): P5Panels {
  const { tier, root } = ctx;
  const pMap = panel(tier, { title: TITLES.map, tier: 'login', span: 12, lock: 'etf' });
  const pHold = panel(tier, { title: TITLES.hold, tier: 'login', span: 6, lock: 'etf_issuers' });
  const pCons = panel(tier, { title: TITLES.cons, tier: 'login', span: 6, lock: 'etf_issuers' });
  root.append(pMap.el, pHold.el, pCons.el);
  const done = Promise.resolve();
  if (pMap.locked) return { map: pMap, hold: pHold, cons: pCons, refresh: () => done, ready: done };

  pMap.setDef(
    '칸 = ETF 하나. 크기 = 순자산(마지막 확정일), 색 = 기간 수익률(분할 보정 종가). 유니버스 = 순자산 50억↑·20일 거래대금 1억↑. 신규 상장(이력이 기간보다 짧음)은 회색.',
  );
  pHold.setDef('운용사 공시 PDF 의 마지막 기준일. 운용사마다 기준일이 다르면 그대로 보인다.');
  pCons.setDef('같은 날 액티브 ETF 2개 이상이 새로 담았거나(신규·TOP10 진입) 수량을 늘린 종목. 1개만은 넣지 않는다.');

  const q = { period: '1M' as MapPeriod, group: 'type' as MapGroup, lev: false, days: '1' as Days };
  const hold = { mode: 'fund' as Mode, code: null as string | null };

  const loadMap = (): Promise<void> =>
    fill(
      pMap,
      () =>
        ctx.source.get<MapData>('etf/map', {
          period: q.period,
          group: q.group,
          lev: q.lev ? 'true' : null,
        }),
      (env) => drawMap(pMap, env, openFund),
      HINT_ETF,
    );
  const loadCons = (): Promise<void> =>
    fill(
      pCons,
      () => ctx.source.get<Consensus>('etf/active-consensus', { days: Number(q.days) }),
      (env) => drawConsensus(pCons, env),
      HINT_HOLD,
    );
  const loadHold = (): Promise<void> => {
    const code = hold.code;
    if (!code) return done;
    return hold.mode === 'fund'
      ? fill(
          pHold,
          () => ctx.source.get<Holdings>(`etf/fund/${code}/holdings`),
          (env) => drawHoldings(pHold, env),
          HINT_HOLD,
        )
      : fill(
          pHold,
          () => ctx.source.get<Holders>(`etf/holders/${code}`),
          (env) => drawHolders(pHold, env),
          HINT_HOLD,
        );
  };

  const modeSeg = seg<Mode>({
    label: '보유 탐색',
    options: HOLD_MODES,
    value: hold.mode,
    onChange: (v) => {
      hold.mode = v;
      void loadHold();
    },
  });
  const input = h('input', {
    attrs: { type: 'text', inputmode: 'text', maxlength: 6, placeholder: '코드 6자리', 'aria-label': '코드', autocomplete: 'off' },
  });
  const msg = h('span', { class: 'count', attrs: { 'aria-live': 'polite' } });
  const submit = (): void => {
    const code = normalizeCode(input.value);
    if (!code) {
      msg.textContent = '코드는 영숫자 6자리';
      return;
    }
    msg.textContent = '';
    hold.code = code;
    void loadHold();
  };
  const form = h(
    'form',
    {
      class: 'inline',
      on: {
        submit: (ev) => {
          ev.preventDefault();
          submit();
        },
      },
    },
    input,
    h('button', { attrs: { type: 'submit' } }, '보기'),
  );
  function openFund(code: string): void {
    hold.mode = 'fund';
    modeSeg.set('fund');
    input.value = code;
    hold.code = code;
    void loadHold();
  }

  filters(
    pMap,
    seg<MapPeriod>({
      label: '기간',
      options: PERIODS,
      value: q.period,
      onChange: (v) => {
        q.period = v;
        void loadMap();
      },
    }).el,
    seg<MapGroup>({
      label: '묶음',
      options: GROUPS,
      value: q.group,
      onChange: (v) => {
        q.group = v;
        void loadMap();
      },
    }).el,
    seg<'hide' | 'show'>({
      label: '레버리지·인버스',
      options: LEV,
      value: 'hide',
      onChange: (v) => {
        q.lev = v === 'show';
        void loadMap();
      },
    }).el,
  );
  filters(pHold, modeSeg.el, form, msg);
  filters(
    pCons,
    seg<Days>({
      label: '기간',
      options: DAYS,
      value: q.days,
      onChange: (v) => {
        q.days = v;
        void loadCons();
      },
    }).el,
  );
  pHold.showMessage('ETF 상장 코드나 종목 코드를 넣으세요 — 맵의 칸을 누르면 그 ETF 구성종목');

  const ready = Promise.all([loadMap(), loadCons()]).then(() => undefined);
  const refresh = (): Promise<void> => Promise.all([loadMap(), loadCons(), loadHold()]).then(() => undefined);
  return { map: pMap, hold: pHold, cons: pCons, refresh, ready };
}
