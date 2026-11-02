// 페이지 3 '상대 시총·연도별 수익률'(stock-dashboard 화면을 옮김, 2026-11-02).
//
// - 로그인(야후·KIS 시총 — 공개판은 자물쇠, 요청하지 않는다). API 'market/relcap'(period 6M·1Y·2Y).
// - 주식봇처럼 쌍마다 작은 차트 카드(이름·기준일·지금 비율, 추이, 기간 최고·최저·위치 꼬리표)를 격자로 한눈에 →
//   아래 주요 지수 연도별 수익률 히트맵(행 = 연도, 열 = 지수, 칸 색 = 부호·크기).
// - 값이 없으면 '—'와 사유(0 으로 그리지 않는다).
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import type { Envelope } from '../../data/source';
import { h, replace } from '../../ui/dom';
import { num, pct } from '../../ui/fmt';
import { lineChart } from '../../ui/line';
import { failText, type Panel, panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import type { StatCard, StatTone } from '../../ui/stat';
import { heatColor } from '../../ui/svg';

type S = components['schemas'];
export type RelCap = S['RelCap'];
type Period = RelCap['period'];
type Pair = S['RelCapPair'];

export const RELCAP_TITLE = '상대 시총·연도별 수익률';
export const RELCAP_HINT = 'global.daily 07:25(야후 일봉·월봉·시총)';

const md = (d: string | null | undefined): string | null => (d ? `${d.slice(5, 7)}/${d.slice(8, 10)}` : null);

function rangeZone(p: Pair): { zone: string | null; tone: StatTone } {
  const r = p.pct_of_range;
  if (r === null) return { zone: null, tone: null };
  if (r >= 80) return { zone: '기간 상단', tone: 'up' };
  if (r <= 20) return { zone: '기간 하단', tone: 'down' };
  return { zone: '기간 중간', tone: null };
}

/** 쌍마다 요약(시험·작은 차트 카드 머리가 같이 쓴다) */
export function relcapCards(d: RelCap): StatCard[] {
  return d.pairs.map((p): StatCard => {
    const z = rangeZone(p);
    return {
      label: p.name,
      asof: md(p.date),
      value: p.latest === null ? null : `${num(p.latest, 1)}%`,
      zone: z.zone,
      tone: z.tone,
      sub: p.error ? p.error : `${d.period} 고점 ${num(p.high ?? null, 1)}% · 저점 ${num(p.low ?? null, 1)}%`,
      subTone: p.error ? 'dim' : null,
      title: `${p.legs_a.join('+')} 시총 ÷ ${p.legs_b.join('+')} 시총(달러 환산) × 100`,
    };
  });
}

/** 쌍 하나의 작은 차트 카드 */
export function pairMini(p: Pair, period: Period): HTMLElement {
  const z = rangeZone(p);
  const box = h(
    'div',
    {
      class: 'mini',
      data: { pair: p.name },
      attrs: { title: `${p.legs_a.join('+')} 시총 ÷ ${p.legs_b.join('+')} 시총(달러 환산) × 100` },
    },
    h(
      'div',
      { class: 'hd' },
      h('b', null, p.name),
      p.date ? h('small', null, md(p.date)) : null,
      h('span', { class: 'v' }, p.latest === null ? '—' : `${num(p.latest, 2)}%`),
    ),
  );
  if (p.series.length === 0) {
    box.append(h('p', { class: 'msg' }, p.error ?? '이력 없음'));
    return box;
  }
  box.append(
    lineChart({
      label: p.name,
      x: p.series.map((s) => s.date),
      series: [{ label: '비율', values: p.series.map((s) => s.value ?? null) }],
      format: (v) => `${num(v, 1)}%`,
      endLabels: false,
      h: 130,
    }).el,
    h(
      'div',
      { class: 'ft' },
      `${period} 최고 ${num(p.high ?? null, 2)}% · 최저 ${num(p.low ?? null, 2)}%`,
      z.zone ? h('span', { class: z.tone ? `tag ${z.tone}` : 'tag' }, z.zone) : null,
    ),
  );
  return box;
}

/** 연도별 수익률 히트맵 — 행 = 연도(최근 위), 열 = 지수. 칸 색 = 부호(상승 빨강·하락 파랑)·크기(±30% 에서 가득) */
export function annualHeat(d: RelCap): HTMLElement {
  const val = (sym: string, y: number): number | null =>
    d.annual.find((r) => r.symbol === sym)?.returns.find((x) => x.year === y)?.pct ?? null;
  const thisYear = d.years[0];
  return h(
    'div',
    { class: 'tbl heat' },
    h(
      'table',
      null,
      h('caption', null, '연말 종가 기준(가격 지수 · 배당 제외), 올해는 작년 말 대비(YTD)'),
      h(
        'thead',
        null,
        h(
          'tr',
          null,
          h('th', { class: 'l', attrs: { scope: 'col' } }, '연도'),
          d.annual.map((r) => h('th', { attrs: { scope: 'col' } }, r.name)),
        ),
      ),
      h(
        'tbody',
        null,
        d.years.map((y) =>
          h(
            'tr',
            null,
            h('td', { class: 'l dim' }, y === thisYear ? `${y} YTD` : String(y)),
            d.annual.map((r) => {
              const v = val(r.symbol, y);
              return h('td', { class: 'hc', css: { background: heatColor(v, 30) } }, v === null ? '—' : pct(v, 1));
            }),
          ),
        ),
      ),
    ),
  );
}

/** 틀만(공개 빌드는 이것만 쓴다 — 자물쇠, 요청 없음) */
export function relcapPanel(ctx: PageContext): Panel {
  const p = panel(ctx.tier, {
    title: RELCAP_TITLE,
    tier: 'login',
    lock: '해외 시세(야후)·KIS 시총 — 로그인 후 표시',
    span: 12,
    src: 'Yahoo·KIS',
    def: '종목 간 시가총액 비율 추이 — 같은 업종 안에서 누가 더 비싸졌는지. 비율 = A 시총 ÷ B 시총(달러 환산) × 100. 과거 값은 현재 주식 수가 그대로였다고 보고 그날 종가로 거슬러 계산한다(자사주 소각·증자 구간은 어긋남). 꼬리표 = 기간 최저~최고 중 지금 위치.',
  });
  ctx.root.append(p.el);
  return p;
}

export async function mountRelcap(ctx: PageContext): Promise<void> {
  const p = relcapPanel(ctx);
  if (p.locked) return;
  const q = { period: '1Y' as Period };
  const controls = h('div', { class: 'filters' });
  const load = async (): Promise<void> => {
    let env: Envelope<RelCap>;
    try {
      env = await ctx.source.get<RelCap>('market/relcap', { period: q.period });
    } catch (err) {
      replace(p.body, controls, h('p', { class: 'msg' }, failText(err, RELCAP_HINT)));
      return;
    }
    p.setSource(env);
    const d = env.data;
    replace(
      p.body,
      controls,
      h(
        'section',
        { class: 'stat-sec' },
        h('h4', null, '상대 시총'),
        h(
          'div',
          { class: 'minis' },
          d.pairs.map((x) => pairMini(x, d.period)),
        ),
      ),
      h('section', { class: 'stat-sec' }, h('h4', null, '주요 지수 연도별 수익률'), annualHeat(d)),
    );
  };
  controls.append(
    seg<Period>({
      label: '기간',
      options: [
        { value: '6M', label: '6M' },
        { value: '1Y', label: '1Y' },
        { value: '2Y', label: '2Y' },
      ],
      value: q.period,
      onChange: (v) => {
        q.period = v;
        void load();
      },
    }).el,
  );
  await load();
}
