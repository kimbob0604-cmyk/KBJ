// 페이지 1 '글로벌 프록시' — 한국 장이 닫힌 동안 한국 주식을 비춰 보는 해외 가격(stock-dashboard 화면을 옮김, 2026-11-02).
//
// - 로그인(야후·Hyperliquid 시세 — 공개판은 자물쇠, 요청하지 않는다). API 'market/global-proxy'(작업 global.proxy 5분).
// - 맨 위 요약 카드: 24시간 선물(국내 종가 대비 %·펀딩률) → 해외 상장 괴리율(ADR·GDR). 아래 표 하나: 지수선물·매크로·미국 ETF.
// - 값이 없으면 '—'와 사유(0 으로 그리지 않는다). 색은 부호(up·down), 뜻은 글자로도 적는다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { num, pct, signClass } from '../../ui/fmt';
import { failText, type Panel } from '../../ui/panel';
import { type StatCard, type StatTone, statGrid } from '../../ui/stat';
import { table } from '../../ui/table';

type S = components['schemas'];
export type GlobalProxy = S['GlobalProxy'];
type Quote = S['ProxyQuote'];

export const GLOBAL_TITLE = '글로벌 프록시 — 장 밖 한국 주식';
export const GLOBAL_HINT = 'global.proxy 5분(Hyperliquid·야후)';
export const GLOBAL_REFRESH_MS = 300_000;

const tone = (v: number | null | undefined): StatTone => {
  const c = signClass(v);
  return c === 'dim' ? null : c;
};

/** 단위 붙인 가격 — 달러·유로는 앞, 나머지는 뒤 */
export function price(q: Quote): string | null {
  if (q.price === null || q.price === undefined) return null;
  const digits = q.price >= 1000 ? 0 : 2;
  const v = num(q.price, digits);
  if (q.unit === '$' || q.unit === '€') return `${q.unit}${v}`;
  if (q.unit === '%') return `${num(q.price, 2)}%`;
  return q.unit ? `${v} ${q.unit}` : v;
}

const hm = (iso: string | null | undefined): string | null => (iso ? iso.slice(11, 16) : null);

export function proxyCards(d: GlobalProxy): StatCard[] {
  const perps = d.perps.map(
    (q): StatCard => ({
      label: `${q.name} 24h`,
      value: q.vs_close_pct === null || q.vs_close_pct === undefined ? price(q) : pct(q.vs_close_pct),
      tone: tone(q.vs_close_pct),
      zone: q.vs_close_pct === null || q.vs_close_pct === undefined ? null : '종가 대비',
      sub: q.error
        ? q.error
        : [
            price(q),
            q.change_pct === null || q.change_pct === undefined ? null : `24h ${pct(q.change_pct)}`,
            q.funding_8h_pct === null || q.funding_8h_pct === undefined ? null : `펀딩 ${pct(q.funding_8h_pct, 3)}/8h`,
          ]
            .filter(Boolean)
            .join(' · '),
      subTone: q.error ? 'dim' : null,
      title: q.vs_label
        ? `Hyperliquid 무기한 선물 vs ${q.vs_label}. 펀딩률 + = 매수 쏠림`
        : 'Hyperliquid 무기한 선물(국내 종가가 없어 대비는 비움)',
    }),
  );
  const listings = d.listings.map(
    (q): StatCard => ({
      label: `${q.name} 괴리`,
      value: q.premium_pct === null || q.premium_pct === undefined ? null : pct(q.premium_pct),
      tone: tone(q.premium_pct),
      zone:
        q.premium_pct === null || q.premium_pct === undefined ? null : q.premium_pct >= 0 ? '프리미엄' : '디스카운트',
      sub: q.error
        ? q.error
        : `${price(q) ?? '—'}${q.fair ? ` vs 환산 ${q.unit}${num(q.fair, 2)}` : ''}${
            q.change_pct === null || q.change_pct === undefined ? '' : ` · 전일 ${pct(q.change_pct)}`
          }`,
      subTone: q.error ? 'dim' : null,
      title: `국내 종가 × ${num(q.ratio ?? null, 2)}주 ÷ 환율 대비 해외 가격`,
    }),
  );
  return [...perps, ...listings];
}

type Row = Quote & { group: string };

export function proxyTable(d: GlobalProxy): HTMLElement {
  const rows: Row[] = [
    ...d.futures.map((q) => ({ ...q, group: '지수선물' })),
    ...d.macro.map((q) => ({ ...q, group: '매크로' })),
    ...d.us_etfs.map((q) => ({ ...q, group: '미국 ETF' })),
  ];
  return table<Row>({
    caption: '지수선물·매크로·미국 ETF — 직전 세션 종가 대비',
    rowKey: (r) => r.symbol,
    rows,
    columns: [
      { key: 'group', label: '구분', align: 'l', cell: (r) => r.group },
      { key: 'name', label: '이름', align: 'l', cell: (r) => r.name },
      { key: 'price', label: '가격', cell: (r) => price(r) ?? r.error ?? null },
      {
        key: 'chg',
        label: '등락',
        cell: (r) => (r.change_pct === null || r.change_pct === undefined ? null : pct(r.change_pct)),
        cls: (r) => signClass(r.change_pct),
      },
    ],
  }).el;
}

export const GLOBAL_DEF =
  '24시간 선물 = Hyperliquid 무기한 선물(달러). 큰 숫자 = 직전 국내 종가(달러 환산) 대비 %, 장 밖에서 내일 시초가 방향을 가늠한다. 펀딩률 + 는 매수 쏠림. 괴리 = 해외 상장(ADR 1주 = 보통주 1/10, 런던 GDR 1주 = 25주, 프랑크푸르트 1주)이 국내 종가 환산값보다 비싸면 프리미엄. 5분마다 받는다.';
export const GLOBAL_LOCK = '해외 시세(야후·Hyperliquid) — 로그인 후 표시';

/** 패널에 그린다. 다시 부르는 함수를 돌려준다(페이지가 주기마다 부른다) */
export function mountGlobal(ctx: PageContext, p: Panel): () => Promise<void> {
  if (p.locked) return () => Promise.resolve();
  return async (): Promise<void> => {
    let env: Envelope<GlobalProxy>;
    try {
      env = await ctx.source.get<GlobalProxy>('market/global-proxy');
    } catch (err) {
      replace(p.body, h('p', { class: 'msg' }, failText(err, GLOBAL_HINT)));
      return;
    }
    p.setSource(env);
    const d = env.data;
    const head = h(
      'p',
      { class: 'msg' },
      `${hm(d.ts) ?? '—'} 기준 · 원/달러 ${num(d.usdkrw ?? null, 1)} · 견준 국내 종가 ${d.base_date ?? '—'}`,
    );
    replace(p.body, head, statGrid(proxyCards(d)), proxyTable(d));
  };
}
