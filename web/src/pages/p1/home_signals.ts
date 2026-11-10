// 첫 화면 '오늘의 신호' 카드(로그인 전용) — `home` 묶음 카드 12장 + 관심종목.
//
// 카드마다 제목·한 줄 메타(기준·건수)·'더 보기 ›'·순위 목록(종목 이름 → 페이지 8). 값은 서버가 고른 그대로 그린다
// (화면에서 다시 계산하지 않는다 — 반올림·단위 바꾸기만 ui/fmt.ts 로). 색은 등락에만.
// - 급등·급락·외국인+기관은 카드 안 두 탭(seg) — 고른 탭은 갱신 뒤에도 남는다(HomeState).
// - 관심종목은 `watchlist`(맥 로컬은 서버 목록, 공개 사이트는 그 데이터 소스가 주는 목록) — 비면 안내 문구.
import type { components } from '../../api/types.gen';
import type { Envelope } from '../../data/source';
import { type Child, h } from '../../ui/dom';
import { DASH, joEok, kstStamp, kstTime, num, pct, shortDate, signClass } from '../../ui/fmt';
import { qValue, visibleRows } from '../../ui/quality';
import { seg } from '../../ui/seg';
import { level } from '../../ui/svg';
import { FEAR_BANDS, koReason, VERDICTS } from '../p3/diag';
import { ratio, usd } from '../p7/common';
import { dartLink, KIND_LABEL, SCOPE_LABEL, SIGNAL_LABEL } from '../p8/common';
import type { HomePanel, SignalKey } from './home_defs';
import {
  bigLine,
  emptyLine,
  type Home,
  joinSub,
  marketKo,
  pill,
  put,
  rankList,
  renderCard,
  signedWon,
  stockName,
} from './home_ui';

type S = components['schemas'];
type Watchlist = S['Watchlist'];

/** 카드 안 탭 선택(갱신해도 남는다 — 페이지마다 하나) */
export interface HomeState {
  movers: 'up' | 'down';
  flows: 'foreign' | 'inst';
}

export const newState = (): HomeState => ({ movers: 'up', flows: 'foreign' });

export const WATCH_HINT = '종목 상세(페이지 8) 머리의 ☆ 단추로 더한다';
const WATCH_MAX = 10;

// ── 공시 ─────────────────────────────────────────────────────

const SIDE_KO = { surprise: '서프', shock: '쇼크' } as const;

function prelimLine(r: S['HomePrelimRow']): HTMLElement {
  return h(
    'li',
    { data: { rcept: r.rcept_no, side: r.side } },
    h('span', { class: `tag ${r.side === 'surprise' ? 'up' : 'down'}` }, SIDE_KO[r.side]),
    h(
      'span',
      { class: 'nm' },
      h('span', null, stockName(r.corp_name, r.stock_code), ' ', h('small', null, joinSub(r.label, SCOPE_LABEL[r.scope]))),
      h(
        'small',
        null,
        `${SIGNAL_LABEL[r.signal] ?? r.signal} · 매출 `,
        qValue(joEok(r.revenue_cur), r.quality) ?? DASH,
        ' (',
        h('span', { class: signClass(r.rev_yoy) }, pct(r.rev_yoy, 1)),
        ') · 영업이익 ',
        qValue(joEok(r.op_cur), r.quality) ?? DASH,
        ' (',
        h('span', { class: signClass(r.op_yoy) }, pct(r.op_yoy, 1)),
        ')',
        r.is_correction ? ' · 정정' : '',
      ),
    ),
    dartLink(r.link, '원문'),
  );
}

function filingLine(r: S['HomeFilingRow']): HTMLElement {
  return h(
    'li',
    { data: { rcept: r.rcept_no } },
    h('span', { class: 'tag dim' }, KIND_LABEL[r.kind] ?? r.kind),
    h(
      'span',
      { class: 'nm' },
      h('span', null, stockName(r.corp_name, r.stock_code), ' ', h('small', null, kstTime(r.first_seen_at))),
      h('small', null, dartLink(r.link, r.title)),
    ),
  );
}

export function drawFilings(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.filings, env, (d) => {
    p.setMeta(
      `${shortDate(d.date)} ${num(d.n_total)}건 · 잠정실적 ${num(d.n_prelim)}건 (서프 ${num(d.n_surprise)} · 쇼크 ${num(d.n_shock)}) · 서프·쇼크 = 전년동기 대비`,
    );
    const { rows: prelims, invalid } = visibleRows(d.prelims);
    if (prelims.length === 0 && d.top.length === 0) {
      put(p.body, emptyLine('고른 공시 없음 — 키워드 점수가 있는 공시가 아직 없다'));
      return invalid;
    }
    put(p.body,
      h('ul', { class: 'hfil hcols2', attrs: { 'aria-label': '오늘의 공시' } }, prelims.map(prelimLine), d.top.map(filingLine)),
    );
    return invalid;
  });
}

// ── 관심종목 ─────────────────────────────────────────────────

/** 관심종목 카드(따로 부른다 — `home` 묶음에는 개인 데이터가 없다). invalid 행 수를 돌려준다 */
export function drawWatch(p: HomePanel, env: Envelope<Watchlist>): number {
  const items = env.data.items;
  p.setMeta(items.length ? `${num(env.data.n)}종목 · 기준 ${kstStamp(env.as_of)}` : '');
  if (items.length === 0) {
    put(p.body, emptyLine(`관심종목이 비어 있다 — ${WATCH_HINT}`));
    return 0;
  }
  const { rows, invalid } = visibleRows(items.map((it) => ({ ...it, quality: it.quality ?? undefined })));
  put(p.body,
    rankList(
      rows.slice(0, WATCH_MAX).map((it) => ({
        name: stockName(it.name, it.code),
        sub: joinSub(marketKo(it.market), it.date ? `${shortDate(it.date)} 종가` : null),
        right: [h('span', { class: 'dim' }, num(it.close)), pill(it.chg_pct, it.quality ?? null)],
        data: { code: it.code },
      })),
      '관심종목',
    ),
    rows.length > WATCH_MAX ? h('p', { class: 'hmore-n' }, `외 ${num(rows.length - WATCH_MAX)}종목 — 페이지 8`) : null,
  );
  return invalid;
}

// ── 시장 폭 ─────────────────────────────────────────────────

export function drawBreadth(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.breadth, env, (d) => {
    p.setMeta(`${shortDate(d.date)} 마감 · 코스피+코스닥 · 이동평균 위 종목 비율`);
    const v = d.above_ma20_pct;
    put(p.body,
      bigLine(v === null ? DASH : pct(v, 1, false), h('small', null, ` 20일선 위 ${num(d.above_ma20_n)} / ${num(d.ma20_base)}종목`)),
      h('div', { class: 'levels' }, level('20일선 위', v ?? 0, v === null ? DASH : pct(v, 1, false))),
    );
    return 0;
  });
}

// ── 거래대금 급증 ────────────────────────────────────────────

export function drawSpike(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.spike, env, (d) => {
    p.setMeta(
      joinSub(
        d.date ? `${shortDate(d.date)} 마감` : null,
        `직전 19일 평균의 ${num(d.spike_min, 1)}배 이상`,
        `거래대금 ${joEok(d.min_turnover)} 이상`,
        `${num(d.n_hits)}종목`,
      ),
    );
    const { rows, invalid } = visibleRows(d.rows);
    if (rows.length === 0) {
      put(p.body, emptyLine('급증한 종목 없음'));
      return invalid;
    }
    put(p.body,
      rankList(
        rows.map((r, i) => ({
          rank: i + 1,
          name: stockName(r.name, r.code),
          sub: joinSub(marketKo(r.market), `거래대금 ${joEok(r.turnover)}`, `시총 ${joEok(r.mktcap)}`),
          right: [h('b', { class: 'mult' }, qValue(`${num(r.spike_mult, 1)}배`, r.quality) ?? DASH), pill(r.chg_pct)],
          data: { code: r.code },
        })),
        '거래대금 급증 순위',
      ),
    );
    return invalid;
  });
}

// ── 급등 · 급락 ─────────────────────────────────────────────

export function drawMovers(p: HomePanel, env: Envelope<Home>, st: HomeState): void {
  renderCard(p, env.data.cards.movers, env, (d) => {
    p.setMeta(`${shortDate(d.date)} 마감 · 시총 ${joEok(d.min_mktcap)} 이상 보통주 ${num(d.n_eligible)}종목`);
    const up = visibleRows(d.up);
    const down = visibleRows(d.down);
    const box = h('div');
    const paint = (): void => {
      const rows = st.movers === 'up' ? up.rows : down.rows;
      box.replaceChildren(
        rows.length
          ? rankList(
              rows.map((r) => ({
                rank: r.rank,
                name: stockName(r.name, r.code),
                sub: joinSub(marketKo(r.market), `시총 ${joEok(r.mktcap)}`),
                right: [h('span', { class: 'dim' }, num(r.close)), pill(r.chg_pct, r.quality)],
                data: { code: r.code },
              })),
              st.movers === 'up' ? '급등 순위' : '급락 순위',
            )
          : emptyLine(st.movers === 'up' ? '오른 종목 없음' : '내린 종목 없음'),
      );
    };
    const tabs = seg<'up' | 'down'>({
      label: '급등 · 급락',
      options: [
        { value: 'up', label: `상승 ${num(up.rows.length)}` },
        { value: 'down', label: `하락 ${num(down.rows.length)}` },
      ],
      value: st.movers,
      onChange: (v) => {
        st.movers = v;
        paint();
      },
    });
    tabs.el.classList.add('htabs');
    paint();
    put(p.body, tabs.el, box);
    return up.invalid + down.invalid;
  });
}

// ── 외국인 + 기관 ────────────────────────────────────────────

export function drawForeignInst(p: HomePanel, env: Envelope<Home>, st: HomeState): void {
  renderCard(p, env.data.cards.foreign_inst, env, (d) => {
    const live = d.basis === 'intraday';
    p.setMeta(`${shortDate(d.date)} ${live ? '장중 가집계(잠정)' : '마감'} · 하루 순매수 상위`);
    const box = h('div');
    const paint = (): void => {
      const rows = st.flows === 'foreign' ? d.foreign : d.inst;
      box.replaceChildren(
        rows.length
          ? rankList(
              rows.map((r, i) => ({
                rank: i + 1,
                name: stockName(r.name, r.code),
                sub: joinSub(marketKo(r.market), r.mktcap === null ? null : `시총 ${joEok(r.mktcap)}`),
                right: [signedWon(r.value, live ? 'estimated' : null), r.chg_pct === null ? null : pill(r.chg_pct)],
                data: { code: r.code },
              })),
              st.flows === 'foreign' ? '외국인 순매수 순위' : '기관 순매수 순위',
            )
          : emptyLine('순매수 종목 없음'),
      );
    };
    const tabs = seg<'foreign' | 'inst'>({
      label: '외국인 · 기관',
      options: [
        { value: 'foreign', label: '외국인' },
        { value: 'inst', label: '기관' },
      ],
      value: st.flows,
      onChange: (v) => {
        st.flows = v;
        paint();
      },
    });
    tabs.el.classList.add('htabs');
    paint();
    put(p.body, tabs.el, box);
    return 0;
  });
}

// ── 신고가 ──────────────────────────────────────────────────

export function drawNewhigh(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.newhigh, env, (d) => {
    const counts = Object.entries(d.counts).map(([k, n]) => `${d.labels[k] ?? k} ${num(n)}`);
    p.setMeta(joinSub(`${shortDate(d.date)} 종가`, ...counts, '시총 순'));
    if (d.rows.length === 0) {
      put(p.body, emptyLine('이날 신고가 종목 없음'));
      return 0;
    }
    put(p.body,
      rankList(
        d.rows.map((r, i) => ({
          rank: i + 1,
          name: stockName(r.name, r.code),
          sub: joinSub(d.labels[r.label] ?? r.label, marketKo(r.market), `시총 ${joEok(r.mktcap)}`),
          right: pill(r.chg_pct),
          data: { code: r.code },
        })),
        '신고가 종목',
      ),
    );
    return 0;
  });
}

// ── GEX ─────────────────────────────────────────────────────

export function drawGex(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.gex, env, (d) => {
    p.setMeta(
      joinSub(
        `${shortDate(d.trade_date)} ${d.session === 'night' ? '야간' : '주간'}`,
        `${d.session_open ? '장중' : '마감'} ${kstTime(d.cycle_at)}`,
        d.stale ? '갱신 지연' : null,
      ),
    );
    put(p.body,
      // 국면은 등락이 아니라 색을 칠하지 않는다(색은 등락에만 — 뜻은 글자로)
      bigLine(h('span', { class: d.regime ? undefined : 'dim' }, d.regime_label ?? '국면 없음')),
      h(
        'dl',
        { class: 'hkv' },
        h('div', null, h('dt', null, '현재가'), h('dd', null, `${num(d.price, 2)}pt`)),
        h('div', null, h('dt', null, 'Flip'), h('dd', null, `${num(d.flip, 2)}pt`)),
        h(
          'div',
          { attrs: { title: '(현재가 − Flip) ÷ 현재가' } },
          h('dt', null, 'Flip 거리'),
          h('dd', { class: signClass(d.flip_distance) }, pct(d.flip_distance, 2)),
        ),
        h('div', null, h('dt', null, '기대변동폭 ±1σ'), h('dd', null, d.expected_move === null ? DASH : `±${num(d.expected_move, 2)}pt`)),
      ),
      d.headline ? h('p', { class: 'hline' }, d.headline) : null,
    );
    return 0;
  });
}

// ── 진단 ────────────────────────────────────────────────────

const FEAR_KEYS = ['extreme_fear', 'fear', 'neutral', 'greed', 'extreme_greed'] as const;

export function drawDiag(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.diag, env, (d) => {
    p.setMeta(joinSub(`${shortDate(d.trade_date)} 기준`, d.caution ? '이벤트 주의' : null));
    const v = d.verdict;
    const fg = d.fear_greed;
    const band = fg?.band ? FEAR_BANDS[FEAR_KEYS.indexOf(fg.band)]?.label : undefined;
    put(p.body,
      bigLine(
        // 판정도 등락이 아니라 색 없이 글자로(색은 등락에만)
        v ? (qValue(VERDICTS[v.label] ?? v.label, v.quality) ?? DASH) : DASH,
        v && v.score !== null ? h('small', null, ` 점수 ${num(v.score, 0, true)}`) : null,
        d.caution ? h('span', { class: 'tag warn' }, '이벤트 주의') : null,
      ),
      v?.top?.length ? h('p', { class: 'hline' }, `근거: ${v.top.join(' · ')}`) : null,
      h(
        'dl',
        { class: 'hkv' },
        h(
          'div',
          null,
          h('dt', null, '국장 공포탐욕'),
          h('dd', null, fg ? (qValue(num(fg.score, 1), fg.quality) ?? DASH) : DASH, band ? h('small', null, ` ${band}`) : null),
        ),
      ),
      fg?.reasons?.length ? h('p', { class: 'hline dim' }, fg.reasons.map(koReason).join(' · ')) : null,
    );
    return 0;
  });
}

// ── ETF ─────────────────────────────────────────────────────

export function drawEtf(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.etf, env, (d) => {
    p.setMeta(
      joinSub(d.start && d.end ? `${shortDate(d.start)}~${shortDate(d.end)}` : null, `${num(d.period)}거래일 순유입 · 유형별`),
    );
    const { rows: top, invalid } = visibleRows(d.top);
    if (d.types.length === 0) {
      put(p.body, emptyLine('유형별 순유입 없음'));
      return invalid;
    }
    put(p.body,
      rankList(
        d.types.map((t) => ({
          name: h('span', { class: 'nm-t' }, t.label),
          sub: `${num(t.n_etfs)}종목`,
          right: signedWon(t.net_inflow),
          data: { type: t.etf_type },
        })),
        'ETF 유형별 순유입',
      ),
      top.length ? h('p', { class: 'hsub' }, '순유입 상위 ETF') : null,
      top.length
        ? rankList(
            top.map((r, i) => ({
              rank: i + 1,
              name: stockName(r.name, r.code),
              sub: r.etf_type_label,
              right: [signedWon(r.net_inflow, r.quality), pill(r.ret_pct)],
              data: { code: r.code },
            })),
            '순유입 상위 ETF',
          )
        : null,
    );
    return invalid;
  });
}

// ── 수출 속보 ────────────────────────────────────────────────

export function drawExport(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.export, env, (d) => {
    p.setMeta(`${d.ym} ${d.span_label}(${d.span_text}) · 관세청 10일 잠정 · 발표 ${shortDate(d.first_seen_at)}`);
    put(p.body,
      bigLine(
        usd(d.usd),
        h(
          'span',
          { class: `pill ${signClass(d.yoy)}`, attrs: { title: '전년 같은 구간 대비(발표 당시 값)' } },
          d.yoy === null ? DASH : ratio(d.yoy),
        ),
      ),
      h('p', { class: 'hline dim' }, d.yoy === null ? `전년 대비 없음 — ${d.yoy_reason ?? '사유 없음'}` : '전년 같은 구간 대비'),
    );
    return 0;
  });
}

// ── 테마 강도 ────────────────────────────────────────────────

export function drawThemes(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.themes, env, (d) => {
    p.setMeta(`${shortDate(d.trade_date)} · ${num(d.period)}일 강도 상위 · 편입 종목 동일가중`);
    if (d.rows.length === 0) {
      put(p.body, emptyLine('테마 강도 없음'));
      return 0;
    }
    put(p.body,
      rankList(
        d.rows.map((r) => {
          const ld = r.stats.leader;
          const sub: Child[] = [r.section];
          if (r.stats.up_ratio !== null) sub.push(` · 상승 ${pct(r.stats.up_ratio * 100, 0, false)}`);
          if (ld) sub.push(' · 대장 ', stockName(ld.name, ld.code), ' ', h('span', { class: signClass(ld.chg_pct) }, pct(ld.chg_pct)));
          return {
            rank: r.rank,
            name: h('span', { class: 'nm-t' }, r.name),
            sub,
            right: pill(r.stats.chg_pct),
            data: { theme: r.id },
          };
        }),
        '테마 강도 순위',
      ),
    );
    return 0;
  });
}

// ── 프로그램 매매 ────────────────────────────────────────────

export function drawProgram(p: HomePanel, env: Envelope<Home>): void {
  renderCard(p, env.data.cards.program, env, (d) => {
    const bars = [
      ['코스피', d.kospi],
      ['코스닥', d.kosdaq],
    ] as const;
    const first = d.kospi ?? d.kosdaq;
    p.setMeta(joinSub(first ? `${shortDate(first.date)} 마감` : null, '일별 순매수 · 장중 누적 없음'));
    let invalid = 0;
    const rows = bars.flatMap(([name, b]) => {
      if (!b) return [];
      if (b.quality === 'invalid') {
        invalid += 1;
        return [];
      }
      return [
        {
          name: h('span', { class: 'nm-t' }, name),
          sub: h('span', null, '차익 ', signedWon(b.arbt_net), ' · 비차익 ', signedWon(b.nabt_net)),
          right: signedWon(b.net, b.quality),
          data: { market: name },
        },
      ];
    });
    put(p.body, rows.length ? rankList(rows, '시장별 프로그램 순매수') : emptyLine('프로그램 순매수 없음'));
    return invalid;
  });
}

/** 묶음 카드 키 → 그리기(관심종목은 따로) */
export type SignalPanels = Record<Exclude<SignalKey, 'watchlist'>, HomePanel>;

export function drawSignals(ps: SignalPanels, env: Envelope<Home>, st: HomeState): void {
  drawFilings(ps.filings, env);
  drawBreadth(ps.breadth, env);
  drawSpike(ps.spike, env);
  drawMovers(ps.movers, env, st);
  drawForeignInst(ps.foreign_inst, env, st);
  drawNewhigh(ps.newhigh, env);
  drawGex(ps.gex, env);
  drawDiag(ps.diag, env);
  drawEtf(ps.etf, env);
  drawExport(ps.export, env);
  drawThemes(ps.themes, env);
  drawProgram(ps.program, env);
}
