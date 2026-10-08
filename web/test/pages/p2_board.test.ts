// 페이지 2 신고가 보드 — 합성 보드 응답으로 표·근접·섹터·히트맵·탐지·랭킹, 기준 토글, 신고가 3축(ADR 0017)·역사적 품질 메모, 공개판 자물쇠.
import { describe, expect, it } from 'vitest';
import page, { AXES_DEF, breakout, countsText, EVENT_LABEL, evidenceText, histNotes, newhighDef } from '../../src/pages/p2_board';
import {
  apiHarness,
  buttonByText,
  choose,
  click,
  fixture,
  flush,
  notFound,
  pageCtx,
  panelByTitle,
  publicHarness,
  selectByLabel,
  text,
} from './support';

async function mountLogin(over: Parameters<typeof apiHarness>[0] = {}) {
  const hz = apiHarness(over);
  const { ctx, root, every } = pageCtx(2, 'login', hz.source);
  await page.mount(ctx);
  return { hz, root, every };
}

interface NewhighFx {
  data: {
    achieved: Record<string, unknown>[];
    hist_scope: Record<string, unknown> | null;
    hist_notes: string[];
    flows: Record<string, { foreign: number | null; inst: number | null; quality: string | null }>;
  };
}

describe('페이지 2 신고가 — 로그인', () => {
  it('네 응답을 부르고 위젯을 그린다', async () => {
    const { hz, root } = await mountLogin();
    expect(hz.paths().sort()).toEqual(['board/events', 'board/newhigh?basis=close', 'board/rankings', 'board/sectors']);

    const nh = panelByTitle(root, '신고가 종목');
    expect(nh.querySelectorAll('tbody tr')).toHaveLength(27);
    expect(text(nh.querySelector('.count'))).toBe('역사적 12 · 52주 22 · 120일 27');
    // 신고가 3축 기준 한 줄(ADR 0017) — 역사적은 '상장 이후', 특정일 표기 없음
    const def = text(nh.querySelector(':scope > .def'));
    expect(def).toContain('역사적 = 상장 이후, 52주 = 달력 52주, 120일 = 120거래일');
    expect(def).not.toContain('2021-10-01');
    expect(def).not.toMatch(/60일|252/);
    // 이력이 상장일에 닿지 않은 종목·원천 바닥 기준 종목은 품질 메모로만
    expect(def).toContain('4종목은 상장일까지 일봉이 닿지 않아');
    expect(def).toContain('5종목은 2010-01-04 이후 최고가 기준');
    expect(text(nh.querySelector('caption'))).toBe('종가 기준 · 거래대금 순');
    // KIS 마감값은 잠정
    expect(text(nh.querySelector('h3 .src'))).toContain('잠정');
    expect(text(nh.querySelector('.notes'))).toContain('KIS 마감값(잠정)');
    const first = nh.querySelector('tbody tr');
    expect(text(first)).toContain('합성종목237');
    expect(text(first)).toContain('역사적');
    expect(text(first)).toContain('0.24%'); // 돌파폭 = −갭
    expect(text(first)).toContain('8,835'); // 억원 그대로

    const near = panelByTitle(root, '신고가 근접');
    expect(near.querySelectorAll('tbody tr')).toHaveLength(20); // 80행 중 20행 + 더 보기
    expect(near.querySelector<HTMLButtonElement>('button.more')?.hidden).toBe(false);

    const sec = panelByTitle(root, '섹터 집계');
    expect(sec.querySelectorAll('.lvl').length).toBeGreaterThan(0);
    expect(text(panelByTitle(root, '테마 히트맵'))).toContain('조선기자재');

    const ev = panelByTitle(root, '탐지 이벤트');
    expect(ev.querySelectorAll('tbody tr')).toHaveLength(20);
    expect(text(ev.querySelector('tbody tr'))).toContain(EVENT_LABEL.material_giveback); // 강도 순
    expect(text(ev)).toContain('[방산수출]'); // 테마 키 → 이름(섹터 응답)
    expect(text(ev.querySelector(':scope > .def'))).toContain('탐지기');

    const rank = panelByTitle(root, '랭킹');
    const sel = selectByLabel(rank, '랭킹');
    expect(sel.options).toHaveLength(4);
    expect(text(rank.querySelector('caption'))).toBe('금일 상승률');
    choose(sel, 't:vol_3d_1m');
    expect(text(rank.querySelector('caption'))).toBe('거래량 3일/1달 순위');
    expect(text(rank.querySelector('tbody tr'))).toContain('177%');

    expect(text(panelByTitle(root, '간밤 미국 신고가'))).toContain('P5');
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
  });

  it('기준·라벨·거래대금 하한 토글 → 쿼리', async () => {
    const { hz, root } = await mountLogin();
    const nh = panelByTitle(root, '신고가 종목');
    click(buttonByText(nh, '고가 기준'));
    await flush();
    expect(hz.paths()).toContain('board/newhigh?basis=high');
    expect(text(nh.querySelector('.count'))).toBe('역사적 10 · 52주 17 · 120일 22');
    expect(text(nh.querySelector('caption'))).toContain('고가 기준');
    choose(selectByLabel(nh, '라벨'), 'hist');
    await flush();
    expect(hz.paths()).toContain('board/newhigh?basis=high&kind=hist');
    choose(selectByLabel(nh, '거래대금 ≥'), '100');
    await flush();
    expect(hz.paths()).toContain('board/newhigh?basis=high&kind=hist&min_turnover_eok=100');
  });

  it('원장 외국인·기관(원)은 억으로, 없으면 —', async () => {
    const nhFx = fixture<NewhighFx>('board_newhigh');
    nhFx.data.flows = { '902370': { foreign: 2_500_000_000, inst: -1_200_000_000, quality: 'ok' } };
    const { root } = await mountLogin({ board_newhigh: nhFx });
    const rows = panelByTitle(root, '신고가 종목').querySelectorAll('tbody tr');
    const tds = rows[0]?.querySelectorAll('td') ?? [];
    expect(text(tds[8])).toBe('+25');
    expect(tds[8]?.className).toContain('up');
    expect(text(tds[9])).toBe('-12');
    expect(text(rows[1]?.querySelectorAll('td')[8])).toBe('—');
  });

  it('역사적 품질 메모가 없으면 축 기준 한 줄만 — 날짜를 지어 적지 않는다', () => {
    const d = fixture<NewhighFx>('board_newhigh').data;
    d.hist_notes = [];
    d.hist_scope = null;
    expect(histNotes(d as never)).toEqual([]);
    expect(newhighDef(d as never).startsWith(AXES_DEF)).toBe(true);
    expect(newhighDef(d as never)).not.toContain('종목은 상장일까지');
    expect(newhighDef(d as never)).not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });

  it('보드가 아직 없으면(404) 예정 작업 — 다른 응답 위젯은 계속', async () => {
    const { root } = await mountLogin({ board_newhigh: () => notFound() });
    expect(text(panelByTitle(root, '신고가 종목').querySelector('.msg'))).toBe('아직 없음 — board.daily 16:00~16:05');
    expect(panelByTitle(root, '탐지 이벤트').querySelectorAll('tbody tr')).toHaveLength(20);
  });

  it('보드 응답 실패는 근접 표에도 — 직전 표를 그대로 두지 않는다', async () => {
    let fail = false;
    const { root, every } = await mountLogin({
      board_newhigh: () => (fail ? notFound() : new Response(JSON.stringify(fixture('board_newhigh')))),
    });
    const near = panelByTitle(root, '신고가 근접');
    expect(near.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
    fail = true;
    every[0]?.fn();
    await flush(10);
    expect(near.querySelectorAll('tbody tr')).toHaveLength(0);
    expect(text(near.querySelector('.msg'))).toBe('아직 없음 — board.daily 16:00~16:05');
  });

  it('고가 기준이면 행의 high_basis 라벨·갭을 읽고, 종가 기준 신규·이어감은 보이지 않는다', async () => {
    const nhFx = fixture<NewhighFx & { data: { filter: Record<string, unknown> } }>('board_newhigh');
    const row = nhFx.data.achieved[0] ?? {};
    row.label = null; // 엔진 기본(종가) 라벨 없음
    row.gap = { hist: 0.5, w52: 0.5, d120: 0.5 };
    row.status = '신규';
    row.close_basis = { label: null, hits: { hist: false, w52: false, d120: false }, gap: row.gap };
    row.high_basis = { label: 'w52', hits: { hist: false, w52: true, d120: true }, gap: { hist: 3, w52: -1.25, d120: -2 } };
    nhFx.data.achieved = [row];
    nhFx.data.filter = { ...nhFx.data.filter, basis: 'high' };
    const { root } = await mountLogin({ board_newhigh: nhFx });
    const tds = panelByTitle(root, '신고가 종목').querySelectorAll('tbody tr td');
    expect(text(tds[2])).toBe('52주');
    expect(text(tds[3])).toBe('—');
    expect(text(tds[5])).toBe('+1.25%');
    expect(breakout(row as never, 'high')).toBe(1.25);
    expect(breakout(row as never, 'close')).toBeNull();
  });

  it('섹터 응답 실패는 섹터 집계·히트맵 두 패널에', async () => {
    const { root } = await mountLogin({ board_sectors: () => notFound() });
    expect(text(panelByTitle(root, '섹터 집계').querySelector('.msg'))).toContain('아직 없음');
    expect(text(panelByTitle(root, '테마 히트맵').querySelector('.msg'))).toContain('아직 없음');
  });

  it('히트맵 축 토글(테마 ↔ board48 섹터)', async () => {
    const { root } = await mountLogin();
    const heat = panelByTitle(root, '테마 히트맵');
    click(buttonByText(heat, '섹터(board48)'));
    expect(text(heat)).toContain('전자제품');
    expect(heat.querySelectorAll('.heat').length).toBeGreaterThan(0);
  });
});

describe('보드 도우미', () => {
  it('돌파폭 = −갭, 라벨 없으면 null', () => {
    expect(breakout({ code: 'A', label: 'w52', gap: { w52: -1.5 } })).toBe(1.5);
    expect(breakout({ code: 'A', label: null, gap: { w52: -1.5 } })).toBeNull();
    expect(breakout({ code: 'A', label: 'hist', gap: { hist: null } })).toBeNull();
  });

  it('개수 줄은 priority 순서', () => {
    expect(
      countsText(
        { labels: { hist: '역사적', w52: '52주' }, priority: ['w52', 'hist'], counts_close: { hist: 1, w52: 2 } },
        'close',
      ),
    ).toBe('52주 2 · 역사적 1');
  });

  it('탐지 근거 문구', () => {
    const labels = { w52: '52주' };
    expect(
      evidenceText(
        {
          type: 'breakout_fail',
          theme: null,
          tickers: [],
          severity: 1,
          evidence: { kind: 'w52', gap_prev: 1, gap_now: 3 },
        },
        labels,
      ),
    ).toBe('52주 갭 1.00% → 3.00%');
    expect(
      evidenceText({ type: 'proximity_cluster', theme: 't', tickers: [], severity: 5, evidence: { n: 5 } }, labels),
    ).toBe('근접 5종목');
    expect(
      evidenceText(
        { type: 'multi_label_high', theme: null, tickers: [], severity: 3, evidence: { labels: ['w52', 'd120'] } },
        labels,
      ),
    ).toBe('52주·d120'); // 사전에 없는 라벨은 키 그대로(지어내지 않는다)
    expect(
      evidenceText(
        { type: 'new_kind', theme: null, tickers: [], severity: 1, evidence: { z: 2.5, note: 'x', obj: {} } },
        labels,
      ),
    ).toBe('z 2.50 · note x');
  });
});

describe('페이지 2 신고가 — 공개', () => {
  it('요청 없이 자물쇠만', async () => {
    const hz = publicHarness();
    const { ctx, root } = pageCtx(2, 'public', hz.source);
    await page.mount(ctx);
    expect(hz.calls).toHaveLength(0);
    expect(root.querySelectorAll('.panel.locked')).toHaveLength(7);
    expect(root.querySelectorAll('table')).toHaveLength(0);
    expect(text(panelByTitle(root, '간밤 미국 신고가').querySelector('h3 .src'))).toBe('준비 중(P5) · 로그인');
  });
});
