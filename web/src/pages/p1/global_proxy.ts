// 페이지 1 '글로벌 프록시' — 한국 장이 닫힌 동안 한국 주식을 비춰 보는 해외 가격(stock-dashboard 화면을 옮김, 2026-11-02).
//
// - 로그인(야후·Hyperliquid 시세 — 공개판은 자물쇠, 요청하지 않는다). API 'market/global-proxy'(작업 global.proxy 5분).
// - 주식봇처럼 묶음마다 소제목·한 줄 설명·카드(큰 숫자 = 가격, 아래 줄 = 색 있는 % + 이름표): 24시간 선물 → 해외 상장
//   괴리율 → 미국 지수선물 → 매크로 → 관련 미국 ETF.
// - 값이 없으면 '—'와 사유(0 으로 그리지 않는다). 색은 부호(up·down), 뜻은 글자로도 적는다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { kstStamp, kstTime, num, pct, signClass } from '../../ui/fmt';
import { failText, type Panel } from '../../ui/panel';
import { type StatCard, type StatTone, statSection } from '../../ui/stat';

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

const has = (v: number | null | undefined): v is number => v !== null && v !== undefined;

/** 공통 카드 — 큰 숫자는 가격, 아래 줄은 색 있는 % + 흐린 이름표(주식봇 글로벌 프록시) */
function quoteCard(q: Quote, lines: StatCard['lines'], foot: string | null = null): StatCard {
  if (q.error) return { label: q.name, value: null, sub: q.error, subTone: 'dim' };
  return { label: q.name, value: price(q), lines: lines?.filter((l) => l.value !== null), foot };
}

const chgLine = (q: Quote, label: string) => ({
  value: has(q.change_pct) ? pct(q.change_pct) : null,
  label,
  tone: tone(q.change_pct),
});

export interface ProxySection {
  title: string;
  desc: string;
  cards: StatCard[];
}

export function proxySections(d: GlobalProxy): ProxySection[] {
  const perps = d.perps.map((q) =>
    quoteCard(
      q,
      [
        {
          value: has(q.vs_close_pct) ? pct(q.vs_close_pct) : null,
          label: `vs ${q.vs_label ?? '국내 종가'}`,
          tone: tone(q.vs_close_pct),
        },
        chgLine(q, '24시간'),
      ],
      has(q.funding_8h_pct) ? `펀딩 ${pct(q.funding_8h_pct, 4)} / 8h` : null,
    ),
  );
  const listings = d.listings.map((q) =>
    quoteCard(
      q,
      [
        chgLine(q, '전일 대비'),
        { value: has(q.premium_pct) ? pct(q.premium_pct) : null, label: '국내 대비 괴리', tone: tone(q.premium_pct) },
      ],
      has(q.ratio)
        ? `1증서 = 보통주 ${num(q.ratio, q.ratio < 1 ? 1 : 0)}주${has(q.fair) ? ` · 환산 ${q.unit}${num(q.fair, 2)}` : ''}`
        : null,
    ),
  );
  const simple = (rows: readonly Quote[]) => rows.map((q) => quoteCard(q, [chgLine(q, '전일 대비')]));
  return [
    {
      title: '24시간 선물 · Hyperliquid',
      desc: '무기한 선물(달러, 코스피200 은 지수). 국내 마지막 종가를 같은 순간 환율로 환산해 견준다. 펀딩률 + 는 매수 쏠림(8시간 기준).',
      cards: perps,
    },
    {
      title: '해외 상장 주식 · 괴리율',
      desc: '국내 종가를 환율과 증서 비율로 환산한 값 대비. SK하이닉스 ADR 1주 = 보통주 1/10주, 삼성전자 GDR 1주 = 보통주 25주.',
      cards: listings,
    },
    { title: '미국 지수선물', desc: '직전 세션 종가 대비.', cards: simple(d.futures) },
    { title: '매크로', desc: '미국 10년물 금리·WTI·원/달러.', cards: simple(d.macro) },
    { title: '관련 미국 ETF', desc: '메모리(DRAM)·한국(EWY)·반도체(SOXX).', cards: simple(d.us_etfs) },
  ];
}

export const GLOBAL_DEF =
  '한국 장이 닫힌 동안 24시간 선물·해외 상장·미국 선물·관련 ETF 로 한국 주식을 가늠한다. 국내 종가 대비 % 가 내일 시초가 방향의 단서.';
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
    const ageH = d.ts ? (ctx.now().getTime() - new Date(d.ts).getTime()) / 3_600_000 : null;
    const stale = ageH !== null && ageH > 3;
    const head = h(
      'p',
      { class: 'stat-desc' },
      `${stale ? kstStamp(d.ts) : kstTime(d.ts)} 기준(5분마다) · 원/달러 ${num(d.usdkrw ?? null, 1)} · 견준 국내 종가 ${d.base_date ?? '—'}`,
      stale ? h('span', { class: 'tag warn' }, `${num(ageH, 0)}시간 지연 — 맥이 잠들었거나 수집이 멈춤`) : null,
    );
    replace(p.body, head, ...proxySections(d).map((x) => statSection(x.title, x.desc, x.cards)));
  };
}
