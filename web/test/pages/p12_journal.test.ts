// 페이지 12 ④ 분석 일지·⑤ 포트폴리오·⑥ 상관·⑦ 추천 성과 + 페이지 8 머리 관심종목 별(묶음 JNL — docs/p8_design.md §4.7·§6).
// 합성 픽스처(tests/unit/api/web_fixtures_p8/journal.py). 404 는 '.msg', 그 밖 실패만 '.msg.err'. 쓰기 채널이 없으면
// 쓰기 단추를 끄고 요청하지 않는다.
import { describe, expect, it } from 'vitest';
import { DataError } from '../../src/data/source';
import { STAR_OFF, STAR_ON, watchStar, watchWriter } from '../../src/pages/p8/header';
import { CORR_HINT, fillCorr } from '../../src/pages/p12/corr';
import {
  detailItems,
  editor,
  fillJournal,
  JOURNAL_HINT,
  NO_WRITER,
  type AnalysisOut,
} from '../../src/pages/p12/journal';
import { averages, fillPicks, type PickList } from '../../src/pages/p12/picks';
import { fillPortfolio, tradeForm } from '../../src/pages/p12/portfolio';
import { panel } from '../../src/ui/panel';
import { JR_NOW, jrFixture, jrHarness, withJournalWriter } from '../fixtures/api/journal/harness';
import { buttonByText, click, flush, pageCtx, text } from './support';

interface Env<T> {
  data: T;
}

function mkPanel(title: string) {
  const p = panel('login', { title, tier: 'login', lock: 'personal', span: 6 });
  document.body.append(p.el);
  return p;
}

function setInput(root: ParentNode, label: string, value: string): void {
  const lab = [...root.querySelectorAll('label')].find((l) => l.textContent.startsWith(label));
  const input = lab?.querySelector('input, select');
  if (!(input instanceof HTMLInputElement || input instanceof HTMLSelectElement)) throw new Error(`칸 없음: ${label}`);
  input.value = value;
  input.dispatchEvent(new Event('input'));
  input.dispatchEvent(new Event('change'));
}

describe('페이지 12 ④ 분석 일지', () => {
  it('목록 → 상세(값 출처 표시) → 마크다운, 쓰기 채널 없으면 저장 꺼짐', async () => {
    const hz = jrHarness();
    const { ctx } = pageCtx(12, 'login', hz.source, { now: () => JR_NOW });
    const p = mkPanel('분석 일지');
    await fillJournal(ctx, p);
    const rows = p.body.querySelectorAll('tbody tr');
    expect(rows).toHaveLength(2);
    expect(text(p.body)).toContain(NO_WRITER);
    expect(buttonByText(p.body, '저장').disabled).toBe(true);
    const first = [...rows].find((r) => text(r).includes('990001'));
    click(first);
    expect(text(p.body)).toContain('12,000원');
    expect(text(p.body)).toContain('KBJ 계산');
    expect(text(p.body)).toContain('컨센서스'); // opm 출처
    click(buttonByText(p.body, '마크다운'));
    await flush();
    const md = p.body.querySelector('pre.jr-md');
    expect(md?.hasAttribute('hidden')).toBe(false);
    expect(md?.textContent).toContain('# 분석 일지 — 합성전자 (990001)');
    expect(hz.paths()).toContain('journal/analysis/1/markdown');
    expect(text(p.body)).not.toMatch(/NaN|undefined/);
  });

  it('prefill → 저장(쓰기 채널) — 고친 칸은 출처가 수기로', async () => {
    const hz = jrHarness();
    const { source, writes } = withJournalWriter(hz.source);
    const { ctx } = pageCtx(12, 'login', source, { now: () => JR_NOW });
    const p = mkPanel('분석 일지');
    await fillJournal(ctx, p);
    setInput(p.body, '종목코드', '990002');
    click(buttonByText(p.body, '값 불러오기(prefill)'));
    await flush();
    expect(text(p.body)).toContain('불러옴 — 2칸');
    click(buttonByText(p.body, '저장'));
    await flush();
    expect(writes[0]?.method).toBe('post');
    expect(writes[0]?.key).toBe('journal/analysis');
    const body = writes[0]?.body as Record<string, unknown>;
    expect(body.code).toBe('990002');
    expect(body.value_sources).toEqual({ price: 'kbj', ret_52w: 'kbj' });
    // 값을 고치면 그 칸 출처는 수기
    const ed = editor('2026-09-15');
    const pre = jrFixture<Env<{ fields: Partial<AnalysisOut> }>>('prefill_990002').data.fields;
    ed.load(pre);
    setInput(ed.el, '현재가', '12345');
    const b = ed.body();
    expect(typeof b).not.toBe('string');
    if (typeof b !== 'string') expect(b.value_sources?.price).toBe('manual');
    setInput(ed.el, '종목코드', 'bad');
    expect(ed.body()).toBe('종목코드 형식(6자리)');
  });

  it('빈 목록은 안내, 실패는 .msg.err', async () => {
    const empty = jrHarness({ empty: true });
    const p = mkPanel('분석 일지');
    await fillJournal(pageCtx(12, 'login', empty.source).ctx, p);
    expect(text(p.body)).toContain(JOURNAL_HINT);
    expect(p.body.querySelector('.msg.err')).toBeNull();
    const broken = jrHarness({ over: { 'journal/analysis': () => new Response('x', { status: 500 }) } });
    const q = mkPanel('분석 일지');
    await fillJournal(pageCtx(12, 'login', broken.source).ctx, q);
    expect(q.body.querySelector('.msg.err')).not.toBeNull();
  });

  it('상세 칸 — 빈 값은 —, 출처 hint', () => {
    const a = jrFixture<Env<AnalysisOut>>('analysis_1').data;
    const items = detailItems(a);
    expect(items.find((i) => i.label === '현재가')?.hint).toBe('KBJ 계산');
    expect(items.find((i) => i.label === '적정가 약세/기본/강세')?.value).toBe('8,100 / 12,100 / 16,900');
  });
});

describe('페이지 12 ⑤ 포트폴리오', () => {
  it('합계·포지션·월별 막대·최근 매매, 매매 넣기는 쓰기 채널로', async () => {
    const hz = jrHarness();
    const { source, writes } = withJournalWriter(hz.source);
    const { ctx } = pageCtx(12, 'login', source, { now: () => JR_NOW });
    const p = mkPanel('포트폴리오·수익률 저널');
    await fillPortfolio(ctx, p);
    const t = text(p.body);
    expect(t).toContain('실현 손익 합');
    expect(t).toContain('보유 종목3개');
    expect(p.body.querySelectorAll('table')).toHaveLength(2);
    expect(p.body.querySelector('svg')).not.toBeNull();
    expect(t).toContain('합성전자 990001');
    expect(text(p.el)).toContain('현금 원장 없음'); // 봉투 notes
    setInput(p.body, '종목코드', '990001');
    setInput(p.body, '가격', '11000');
    setInput(p.body, '수량', '1');
    click(buttonByText(p.body, '매매 넣기'));
    await flush();
    expect(writes[0]).toMatchObject({ method: 'post', key: 'journal/trades' });
    expect(writes[0]?.body).toMatchObject({ code: '990001', side: 'buy', price: 11000, qty: 1, fee: 0 });
  });

  it('보유보다 많이 팔면 서버 거절 문구만(입력값 없음)', async () => {
    const hz = jrHarness();
    const { source } = withJournalWriter(hz.source, () => new DataError('보유 수량보다 많이 판다', 422));
    const p = mkPanel('포트폴리오·수익률 저널');
    await fillPortfolio(pageCtx(12, 'login', source, { now: () => JR_NOW }).ctx, p);
    setInput(p.body, '종목코드', '990001');
    setInput(p.body, '가격', '11000');
    setInput(p.body, '수량', '999');
    click(buttonByText(p.body, '매매 넣기'));
    await flush();
    const err = p.body.querySelector('.msg.err');
    expect(err).not.toBeNull();
    expect(text(err)).not.toContain('999');
  });

  it('입력 칸 검사 — 코드·수량', () => {
    const f = tradeForm('2026-09-15');
    expect(f.body()).toBe('종목코드 형식(6자리)');
    setInput(f.el, '종목코드', '990001');
    setInput(f.el, '가격', '100');
    expect(f.body()).toBe('수량(1 이상 정수)');
  });

  it('매매 없으면 안내 + 입력 칸', async () => {
    const p = mkPanel('포트폴리오·수익률 저널');
    await fillPortfolio(pageCtx(12, 'login', jrHarness({ empty: true }).source).ctx, p);
    expect(text(p.body)).toContain('아직 없음 — 매매 기록 없음');
    expect(buttonByText(p.body, '매매 넣기').disabled).toBe(true);
  });
});

describe('페이지 12 ⑥ 상관관계', () => {
  it('히트맵 — 관심/보유·60/120 을 바꾸면 다시 요청', async () => {
    const hz = jrHarness();
    const p = mkPanel('상관관계');
    await fillCorr(pageCtx(12, 'login', hz.source).ctx, p);
    expect(p.body.querySelectorAll('rect').length).toBeGreaterThanOrEqual(9);
    click(buttonByText(p.body, '120일'));
    await flush();
    click(buttonByText(p.body, '보유'));
    await flush();
    expect(hz.paths()).toEqual([
      'portfolio/correlation?n=60&set=watchlist',
      'portfolio/correlation?n=120&set=watchlist',
      'portfolio/correlation?n=120&set=holdings',
    ]);
  });

  it('종목 2개 미만(404)은 .msg 안내', async () => {
    const p = mkPanel('상관관계');
    await fillCorr(pageCtx(12, 'login', jrHarness({ empty: true }).source).ctx, p);
    expect(p.body.querySelector('.msg.err')).toBeNull();
    expect(text(p.body)).toMatch(/2개 미만/);
    expect(CORR_HINT).toContain('2개 미만');
  });
});

describe('페이지 12 ⑦ 추천 성과', () => {
  it('기간 평균(값 있는 것만 n)·표, 이관분 없으면 .msg', async () => {
    const p = mkPanel('추천 성과');
    await fillPicks(pageCtx(12, 'login', jrHarness().source).ctx, p);
    const items = jrFixture<Env<PickList>>('picks').data.items;
    const avg = averages(items);
    expect(avg.d1.n).toBe(2);
    expect(avg.d7.n).toBe(1);
    expect(p.body.querySelectorAll('tbody tr')).toHaveLength(2);
    expect(text(p.body)).toContain('(n=1)');
    const q = mkPanel('추천 성과');
    await fillPicks(pageCtx(12, 'login', jrHarness({ empty: true }).source).ctx, q);
    expect(q.body.querySelector('.msg.err')).toBeNull();
    expect(text(q.body)).toContain('이관분 없음');
  });
});

describe('페이지 8 머리 관심종목 별', () => {
  it('쓰기 채널 없으면 그리지 않고, 있으면 put·del 로 토글', async () => {
    expect(watchWriter(jrHarness().source)).toBeNull();
    const { source, writes } = withJournalWriter(jrHarness().source);
    const w = watchWriter(source);
    expect(w).not.toBeNull();
    if (!w) return;
    const el = watchStar('990001', false, w);
    document.body.append(el);
    const btn = el.querySelector('button');
    expect(btn?.textContent).toBe(STAR_OFF);
    click(btn);
    await flush();
    expect(btn?.textContent).toBe(STAR_ON);
    expect(btn?.getAttribute('aria-pressed')).toBe('true');
    click(btn);
    await flush();
    expect(writes.map((x) => `${x.method} ${x.key}`)).toEqual([
      'put watchlist/main/990001',
      'del watchlist/main/990001',
    ]);
    const failing = withJournalWriter(jrHarness().source, () => new DataError('관심종목 쓰기 거절', 404));
    const w2 = watchWriter(failing.source);
    if (!w2) return;
    const el2 = watchStar('990009', false, w2);
    click(el2.querySelector('button'));
    await flush();
    expect(el2.querySelector('button')?.textContent).toBe(STAR_OFF);
    expect(text(el2)).toContain('거절');
  });
});
