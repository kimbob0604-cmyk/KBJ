// 페이지 8 '리포트' 탭(dart-report — 엑셀과 같은 입력·같은 셈, docs/p4_design.md §4.6·§6.2).
//
// 블록 5개: 분기 실적(막대 + OPM 보조축) · 연간 비용구조(누적 막대) · 이익 브릿지(워터폴 — 잔차 행 강조) ·
// 수주 현황(표 + 합계) · 분기 비용구조(100% 누적) + 경고 줄 + 엑셀 내려받기.
// - 블록 금액은 이미 **억원 실수**(엑셀 칸과 같은 값 — R 묶음 blocks.py). 화면은 반올림만 한다.
// - 블록 모양은 JSON(형 정보 없음)이라 읽을 때 검사한다 — 모양이 틀린 블록은 그 블록만 '형식 오류'(다른 블록은 계속).
// - 로그인: 'stock/<코드>/report' + 내려받기 주소 hrefOf(reportXlsxKey). 공개: 데모 종목(corp_index.report)만
//   reports/<코드>.json, 그 밖은 '로그인 또는 데모 종목' 안내.
import { type Envelope, hrefOf, reportXlsxKey } from '../../data/source';
import { append, h } from '../../ui/dom';
import { DASH, kstDate, num, type Num, pct } from '../../ui/fmt';
import { kv } from '../../ui/kv';
import type { Panel } from '../../ui/panel';
import { type StackSeries, stackBar } from '../../ui/stackbar';
import { table } from '../../ui/table';
import { type WaterfallStep, waterfall } from '../../ui/waterfall';
import { fill } from '../p1_market';
import { col, comboChart, DART_VIEWER, dartLink, eok1, errLine, HINT, mkPanel, NO_PUB_FIN, type S, tabGrid, type TabCtx, tag } from './common';

type Report = S['ReportView'];
type Obj = Record<string, unknown>;

// ── 블록 읽기(JSON 검사) ──────────────────────────────────

const obj = (v: unknown): Obj | null => (typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Obj) : null);
const numOf = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const nums = (v: unknown): Num[] => (Array.isArray(v) ? v.map(numOf) : []);
const str = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '');
const objs = (v: unknown): Obj[] => (Array.isArray(v) ? v.map(obj).filter((r): r is Obj => r !== null) : []);
/** 버킷 이름 → 값 배열(JSON 순서 = 엑셀 열 순서) */
const buckets = (v: unknown): StackSeries[] => Object.entries(obj(v) ?? {}).map(([label, x]) => ({ label, values: nums(x) }));
/** 엑셀 비율 칸(0.123) → % */
const ratio = (v: Num): string => (typeof v === 'number' ? pct(v * 100, 1, false) : DASH);
const eokFmt = (x: number): string => `${num(x, 1)}억`;

class Shape extends Error {}

function need(o: Obj | null, key: string): Obj {
  if (!o || !Array.isArray(o[key])) throw new Shape(key);
  return o;
}

// ── 블록 그리기 ───────────────────────────────────────────

export function quarterlyBlock(v: unknown): HTMLElement {
  const rows = objs(need(obj(v), 'rows').rows);
  if (!rows.length) return h('p', { class: 'msg' }, '분기 실적 없음');
  return h(
    'div',
    null,
    comboChart({
      label: '분기 실적 — 매출과 영업이익률',
      categories: rows.map((r) => str(r.label)),
      bars: rows.map((r) => numOf(r.revenue)),
      line: rows.map((r) => {
        const x = numOf(r.opm);
        return x === null ? null : x * 100;
      }),
      barName: '매출(억원)',
    }),
    table<Obj>({
      caption: '분기 실적(억원)',
      columns: [
        col('분기', (r) => h('span', null, str(r.label), r.derived === true ? tag('차분 불가', 'dim') : null), true),
        col('매출', (r) => eok1(numOf(r.revenue))),
        col('영업이익', (r) => eok1(numOf(r.operating_income))),
        col('순이익', (r) => eok1(numOf(r.net_income))),
        col('OPM', (r) => ratio(numOf(r.opm))),
      ],
      rows: [...rows].reverse(),
      pageSize: 8,
      empty: '분기 실적 없음',
    }).el,
  );
}

export function annualCostsBlock(v: unknown): HTMLElement {
  const o = need(obj(v), 'columns');
  if (o.has_costs === false) return h('p', { class: 'msg' }, '연간 비용 분해 없음(주석 비용 표 없음)');
  const cols = (o.columns as unknown[]).map(str);
  const lines: [string, Num[], (x: Num) => string][] = [
    ['매출', nums(o.revenue), eok1],
    ...buckets(o.buckets).map((b): [string, Num[], (x: Num) => string] => [b.label, [...b.values], eok1]),
    ['비용 합계', nums(o.total), eok1],
    ['영업이익(역산)', nums(o.operating_income), eok1],
    ['OPM', nums(o.opm), ratio],
  ];
  return h(
    'div',
    null,
    stackBar({ label: '연간 비용 구성(억원)', categories: cols, series: buckets(o.buckets), format: eokFmt }),
    table({
      caption: '연간 비용 구성(억원)',
      columns: [col('항목', (r: (typeof lines)[number]) => r[0], true), ...cols.map((c, j) => col(c, (r: (typeof lines)[number]) => r[2](r[1][j])))],
      rows: lines,
      pageSize: 20,
    }).el,
  );
}

export function bridgeBlock(v: unknown): HTMLElement {
  const o = need(obj(v), 'steps');
  const steps: WaterfallStep[] = objs(o.steps).map((r) => ({
    label: str(r.name),
    value: numOf(r.value),
    kind: r.kind === 'total' ? 'total' : 'delta',
    residual: str(r.note) !== '',
  }));
  if (!steps.length) return h('p', { class: 'msg' }, '브릿지 없음(영업외손익 주석 없음)');
  const res = numOf(o.residual);
  return h(
    'div',
    null,
    waterfall({ label: `이익 브릿지 ${str(o.label)}(억원)`, steps, format: (x, st) => `${num(x, 1, st.kind !== 'total')}억` }),
    res ? h('div', { class: 'check fail', data: { check: 'residual' } }, `잔차(주석으로 설명 안 된 차이) ${num(res, 1, true)}억`) : null,
  );
}

export function ordersBlock(v: unknown): HTMLElement {
  const o = need(obj(v), 'rows');
  return h(
    'div',
    null,
    kv([
      { label: '수주 합계', value: `${eok1(numOf(o.total_eok))}억` },
      { label: 'NIPA 비중', value: ratio(numOf(o.nipa_ratio)) },
    ]),
    table<Obj>({
      caption: '수주 현황(단일판매·공급계약)',
      columns: [
        col('고객사', (r) => str(r['고객사']), true),
        col('사업내용', (r) => str(r['사업내용']), true),
        col('수주액(억원)', (r) => eok1(numOf(r['수주액(억원)']))),
        col('기간', (r) => `${str(r['시작일'])} ~ ${str(r['종료일'])}`),
        col(
          '공시',
          (r) => {
            const no = str(r['접수번호']);
            return h('span', null, no ? dartLink(`${DART_VIEWER}dsaf001/main.do?rcpNo=${no}`, no) : DASH, str(r['정정']) ? tag('정정', 'warn') : null);
          },
          true,
        ),
      ],
      rows: objs(o.rows),
      pageSize: 10,
      empty: '수주 공시 없음',
    }).el,
  );
}

export function quarterlyCostsBlock(v: unknown): HTMLElement {
  const o = need(obj(v), 'columns');
  const cols = (o.columns as unknown[]).map(str);
  if (!cols.length) return h('p', { class: 'msg' }, '분기 비용 분해 없음');
  const beh = obj(o.behavior) ?? {};
  const cm = nums(o.contribution_margin);
  return h(
    'div',
    null,
    stackBar({
      label: '분기 비용 구성 — 비용 합계 대비 %',
      categories: cols,
      series: buckets(o.buckets).map((b) => ({ ...b, label: str(beh[b.label]) ? `${b.label}(${str(beh[b.label])})` : b.label })),
      mode: 'pct',
      format: eokFmt,
    }),
    h('p', { class: 'def' }, `공헌이익률 ${cols.map((c, j) => `${c} ${ratio(cm[j] ?? null)}`).slice(-4).join(' · ')}`),
  );
}

const BLOCKS: readonly [keyof S['ReportBlocks'], string, (v: unknown) => HTMLElement][] = [
  ['quarterly', '분기 실적', quarterlyBlock],
  ['annual_costs', '연간 비용구조', annualCostsBlock],
  ['bridge', '이익 브릿지', bridgeBlock],
  ['orders', '수주 현황', ordersBlock],
  ['quarterly_costs', '분기 비용구조', quarterlyCostsBlock],
];

/** 리포트 블록 다섯 + 경고 + 내려받기. 한 블록이 틀려도 나머지는 그린다 */
export function drawReport(p: Panel, d: Report, xlsxHref: string | null, asked: string): void {
  append(p.body, [
    h(
      'p',
      { class: 'dim' },
      `${kstDate(d.built_on)} 생성 · ${d.fs_div_used === 'OFS' ? '별도' : '연결'} · ${d.year_from}~${d.year_to} · ${d.unit}${d.code !== asked ? ` · 회사(보통주 ${d.code}) 리포트` : ''}`,
    ),
    d.xlsx_available && xlsxHref
      ? h('p', null, h('a', { class: 'btn', attrs: { href: xlsxHref, download: '' }, data: { download: 'xlsx' } }, '엑셀 내려받기'))
      : h('p', { class: 'dim' }, '엑셀 파일 없음'),
    d.warnings.map((w) => h('div', { class: 'check fail', data: { check: 'warning' } }, w)),
  ]);
  for (const [key, title, draw] of BLOCKS) {
    const sec = h('section', { data: { section: key } }, h('h4', null, title));
    const v = d.blocks[key];
    try {
      // 블록이 비어 있음(null — 공개 파일은 그 블록을 만들지 못했을 때 null)은 형식 오류가 아니다
      sec.append(v === null || v === undefined ? h('p', { class: 'msg' }, `${title} 없음`) : draw(v));
    } catch (err) {
      if (!(err instanceof Shape)) console.error('[kbj] 리포트 블록 오류', key, err);
      sec.append(h('p', { class: 'msg err' }, `${title} — 형식 오류(이 블록만 건너뜀)`));
    }
    p.body.append(sec);
  }
}

export async function mount(t: TabCtx, el: HTMLElement): Promise<void> {
  const p = mkPanel(t, { title: 'dart-report', tier: 'public', span: 12 });
  tabGrid(el, p);
  p.setDef('엑셀 리포트와 같은 입력·같은 셈. 잔차 = 주석으로 나누지 못한 영업외손익.');
  const href = hrefOf(t.source, reportXlsxKey(t.tier, t.code));
  const draw = (env: Envelope<Report>): number => {
    drawReport(p, env.data, href, t.code);
    return 0;
  };
  if (t.tier === 'login') {
    await fill(p, () => t.get<Report>(`stock/${t.code}/report`), draw, HINT.report);
    return;
  }
  // 공개: 데모 종목만(corp_index.report) — 그 밖은 요청하지 않는다
  try {
    const corp = await t.corp();
    if (!corp?.report) {
      p.showMessage(corp ? '리포트는 로그인 화면 또는 공개 데모 종목만' : NO_PUB_FIN);
      return;
    }
  } catch (err) {
    p.showMessage(errLine(err, HINT.public));
    return;
  }
  await fill(p, () => t.get<Report>(`reports/${t.code}`), draw, HINT.public);
}
