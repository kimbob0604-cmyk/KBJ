// 경제 캘린더 패널(MAC — ./p5/calendar, 페이지 5·6 공용): 기간 거르기(화면)·발표 결과·예상치(로그인만)·출처 이름·오류 격리.
import { describe, expect, it } from 'vitest';
import { type CalRow, calendarColumns, createCalendar, inWindow, sourceLabel } from '../../src/pages/p5/calendar';
import { json } from '../helpers';
import { MAC_NOW, macLoginHarness, macPublicHarness, macroFixture } from '../fixtures/api/macro/harness';
import { buttonByText, click, flush, text } from './support';

function mountCal(tier: 'login' | 'public', over: Record<string, (() => Response) | undefined> = {}) {
  const hz = tier === 'login' ? macLoginHarness(over) : macPublicHarness(over);
  const w = createCalendar({ tier, source: hz.source, now: () => MAC_NOW });
  document.body.append(w.panel.el);
  return { hz, w };
}

const rows = (): CalRow[] => macroFixture<{ data: { rows: CalRow[] } }>('calendar').data.rows;

describe('경제 캘린더 — 로그인', () => {
  it('이번 주(기본) — 예상치 열, FF 행은 출처 ForexFactory', async () => {
    const { hz, w } = mountCal('login');
    await w.load();
    expect(hz.paths()).toEqual(['macro/calendar']);
    const heads = [...w.panel.el.querySelectorAll('th')].map((t) => text(t));
    expect(heads).toEqual(['날짜', '나라', '일정', '중요도', '발표 결과', '예상치', '출처']);
    expect(text(w.panel.el.querySelector('caption'))).toContain('이번 주');
    // 11-17(화) 기준 이번 주(11-17~11-23): 합성 일정 중 해당 없음 → 빈 줄
    expect(text(w.panel.el)).toContain('이 기간 일정 없음');
    click(buttonByText(w.panel.el, '전체'));
    await flush();
    const body = [...w.panel.el.querySelectorAll('tbody tr')].map((r) => text(r));
    expect(body.some((r) => r.includes('ForexFactory(예상치)') && r.includes('150'))).toBe(true);
    expect(body.some((r) => r.includes('금융통화위원회') && r.includes('★★★'))).toBe(true);
    expect(hz.paths()).toEqual(['macro/calendar']); // 거르기는 다시 부르지 않는다
  });

  it('실패는 이 패널 안 한 줄(본문 없이)', async () => {
    const { w } = mountCal('login', { 'macro/calendar': () => json({ detail: 'secret' }, 500) });
    await w.load();
    expect(text(w.panel.el)).toContain('HTTP 500');
    expect(text(w.panel.el)).not.toContain('secret');
  });
});

describe('경제 캘린더 — 공개', () => {
  it('공개 파일 하나 — 예상치 열 없음, 예상치 행 없음', async () => {
    const { hz, w } = mountCal('public');
    await w.load();
    expect(hz.paths()).toEqual(['./data/macro/calendar.json']);
    click(buttonByText(w.panel.el, '전체'));
    await flush();
    expect([...w.panel.el.querySelectorAll('th')].map((t) => text(t))).not.toContain('예상치');
    expect(text(w.panel.el)).not.toContain('ForexFactory');
    expect(w.panel.el.querySelectorAll('tbody tr').length).toBeGreaterThan(0);
  });
});

describe('도우미', () => {
  it('기간 거르기 — 오늘·이번 주(오늘~6일 뒤)·전체', () => {
    const r = rows();
    expect(inWindow(r, 'all', '2026-11-17')).toHaveLength(r.length);
    expect(inWindow(r, 'today', '2026-11-27').map((x) => x.event_id)).toEqual(['KR:bok_rate:2026-11-27:bok']);
    expect(inWindow(r, 'week', '2026-11-27').map((x) => x.date)).toEqual(['2026-11-27', '2026-12-01', '2026-12-03']);
  });
  it('출처 이름·열', () => {
    expect(sourceLabel('fred+ff')).toBe('FRED + ForexFactory(예상치)');
    expect(calendarColumns(false).map((c) => c.key)).not.toContain('forecast');
  });
});
