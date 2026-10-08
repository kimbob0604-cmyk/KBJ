// UI 부품 — h()·표·선택·패널·칩·SVG
import { describe, expect, it, vi } from 'vitest';
import { AuthRequiredError, DataError, LockedError } from '../src/data/source';
import { chip } from '../src/ui/chip';
import { clear, h, isEditable, replace } from '../src/ui/dom';
import { LOCK_REASONS, lockText } from '../src/ui/lock';
import { errorText, panel } from '../src/ui/panel';
import { seg, select } from '../src/ui/seg';
import { barChart, centerLevel, heatColor, level, scale, sparkline, svgBox } from '../src/ui/svg';
import { table } from '../src/ui/table';
import { envelope } from './helpers';

describe('h()', () => {
  it('글자는 textContent 로만(태그가 해석되지 않는다)', () => {
    const el = h('div', null, '<img src=x onerror=alert(1)>');
    expect(el.children).toHaveLength(0);
    expect(el.textContent).toBe('<img src=x onerror=alert(1)>');
  });

  it('class·data·attrs·css·on', () => {
    const click = vi.fn();
    const el = h(
      'button',
      { class: 'btn', data: { page: 4 }, attrs: { type: 'button', disabled: false, title: 't' }, css: { width: '50%' }, on: { click } },
      '눌러',
    );
    expect(el.className).toBe('btn');
    expect(el.dataset.page).toBe('4');
    expect(el.hasAttribute('disabled')).toBe(false);
    expect(el.style.width).toBe('50%');
    el.click();
    expect(click).toHaveBeenCalledOnce();
  });

  it('style 속성·on* 속성은 거부(CSP·XSS)', () => {
    expect(() => h('div', { attrs: { style: 'color:red' } })).toThrow('style');
    expect(() => h('div', { attrs: { onclick: 'x()' } })).toThrow('이벤트');
  });

  it('배열 자식·null 건너뛰기·clear·replace', () => {
    const el = h('ul', null, [h('li', null, 1), null, false, [h('li', null, 2)]]);
    expect(el.querySelectorAll('li')).toHaveLength(2);
    replace(el, h('li', null, 'x'));
    expect(el.textContent).toBe('x');
    clear(el);
    expect(el.childNodes).toHaveLength(0);
  });

  it('입력 칸 판별', () => {
    expect(isEditable(h('input'))).toBe(true);
    expect(isEditable(h('select'))).toBe(true);
    expect(isEditable(h('div'))).toBe(false);
    const ce = h('div', { attrs: { contenteditable: 'true' } }, h('span'));
    expect(isEditable(ce.firstElementChild)).toBe(true);
    expect(isEditable(null)).toBe(false);
  });
});

describe('표', () => {
  type Row = { code: string; v: number };
  const rows: Row[] = Array.from({ length: 120 }, (_, i) => ({ code: String(i).padStart(6, '0'), v: i }));
  const columns = [
    { key: 'code', label: '종목', align: 'l' as const, cell: (r: Row) => r.code },
    { key: 'v', label: '값', cell: (r: Row) => (r.v === 3 ? null : String(r.v)), cls: (r: Row) => (r.v > 0 ? 'up' : undefined) },
  ];

  it('머리 th scope=col, 기본 50행 + 더 보기', () => {
    const t = table({ columns, rows });
    expect([...t.el.querySelectorAll('th')].map((th) => th.getAttribute('scope'))).toEqual(['col', 'col']);
    expect(t.el.querySelectorAll('tbody tr')).toHaveLength(50);
    const more = t.el.querySelector<HTMLButtonElement>('.more');
    expect(more?.hidden).toBe(false);
    more?.click();
    expect(t.el.querySelectorAll('tbody tr')).toHaveLength(100);
    more?.click();
    expect(t.el.querySelectorAll('tbody tr')).toHaveLength(120);
    expect(more?.hidden).toBe(true);
  });

  it('빈 칸은 —, 부호 색 class', () => {
    const t = table({ columns, rows: rows.slice(0, 5) });
    const cells = t.el.querySelectorAll('tbody tr:nth-child(4) td');
    expect(cells[1]?.textContent).toBe('—');
    expect(cells[1]?.className).toBe('up');
  });

  it('행 선택: 클릭·Enter, aria-selected', () => {
    const onSelect = vi.fn();
    const t = table({ columns, rows: rows.slice(0, 3), rowKey: (r) => r.code, onSelect });
    const trs = t.el.querySelectorAll<HTMLTableRowElement>('tbody tr');
    trs[1]?.click();
    expect(onSelect).toHaveBeenLastCalledWith(rows[1]);
    expect(trs[1]?.getAttribute('aria-selected')).toBe('true');
    trs[2]?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
    expect(onSelect).toHaveBeenLastCalledWith(rows[2]);
    expect(trs[1]?.getAttribute('aria-selected')).toBe('false');
    expect(trs[2]?.tabIndex).toBe(0);
  });

  it('행이 없으면 안내 한 줄, update 로 바꾼다', () => {
    const t = table({ columns, rows: [], empty: '기준 넘는 ETF 없음' });
    expect(t.el.querySelector('tbody')?.textContent).toBe('기준 넘는 ETF 없음');
    t.update(rows.slice(0, 2));
    expect(t.el.querySelectorAll('tbody tr')).toHaveLength(2);
  });
});

describe('선택 버튼·상자', () => {
  it('seg: aria-pressed 하나만, 바뀔 때만 onChange', () => {
    const onChange = vi.fn();
    const s = seg({
      label: '기간',
      options: [
        { value: '1', label: '1일' },
        { value: '5', label: '5일' },
      ],
      value: '1',
      onChange,
    });
    const [b1, b5] = [...s.el.querySelectorAll('button')];
    b1?.click();
    expect(onChange).not.toHaveBeenCalled();
    b5?.click();
    expect(onChange).toHaveBeenCalledWith('5');
    expect(s.value()).toBe('5');
    expect([b1, b5].map((b) => b?.getAttribute('aria-pressed'))).toEqual(['false', 'true']);
    expect(s.el.getAttribute('role')).toBe('group');
  });

  it('select: 이름표 + change', () => {
    const onChange = vi.fn();
    const s = select({
      label: '시장',
      options: [
        { value: 'all', label: '전체' },
        { value: 'KOSPI', label: '코스피' },
      ],
      value: 'KOSPI',
      onChange,
    });
    expect(s.value()).toBe('KOSPI');
    s.select.value = 'all';
    s.select.dispatchEvent(new Event('change'));
    expect(onChange).toHaveBeenCalledWith('all');
    expect(s.el.tagName).toBe('LABEL');
  });
});

describe('패널', () => {
  it('공개 빌드의 로그인 위젯 = 자물쇠(본문 없음, setSource 무시)', () => {
    const p = panel('public', { title: '시장폭', tier: 'login', lock: 'quote_based', span: 4 });
    expect(p.locked).toBe(true);
    expect(p.el.classList.contains('locked')).toBe(true);
    expect(p.el.classList.contains('span-4')).toBe(true);
    expect(p.el.querySelector('.lockmsg')?.textContent).toBe(`LOGIN${LOCK_REASONS.quote_based}`);
    expect(p.el.contains(p.body)).toBe(false);
    p.setSource(envelope(1));
    expect(p.head.querySelector('.src')?.textContent).toBe('');
  });

  it('로그인 빌드의 로그인 위젯은 열린다 + 원천 줄·stale 머리·notes', () => {
    const p = panel('login', { title: '시장폭', tier: 'login', def: '정의' });
    expect(p.locked).toBe(false);
    p.setSource(envelope(1, { quality: 'stale', notes: ['NXT 미포함'] }), 2);
    expect(p.head.querySelector('.src')?.textContent).toBe('KRX · 10/07 15:30 · 지연');
    expect(p.el.classList.contains('stale')).toBe(true);
    expect(p.el.querySelector('.notes')?.textContent).toBe('NXT 미포함 · 검산 실패 2행 제외');
    expect(p.el.querySelector(':scope > .def')?.textContent).toBe('정의');
    p.setDef('새 정의');
    expect([...p.el.querySelectorAll(':scope > .def')].map((d) => d.textContent)).toEqual(['새 정의']);
  });

  it('준비 중 패널', () => {
    const p = panel('login', { title: '간밤 미국 신고가', tier: 'login', pending: 'P5', pendingNote: '미국장' });
    expect(p.pending).toBe(true);
    expect(p.head.querySelector('.src')?.textContent).toBe('준비 중(P5)');
    expect(p.el.querySelector('.pending-box')?.textContent).toContain('P5 단계에서 채웁니다');
  });

  it('오류는 그 패널에만, 본문 없이 짧게', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const p = panel('login', { title: 'x', tier: 'login' });
    p.showError(new DataError('불러오지 못함 (HTTP 500)', 500));
    expect(p.body.textContent).toBe('불러오지 못함 (HTTP 500)');
    expect(errSpy).not.toHaveBeenCalled();
    p.showError(new Error('예상 밖'));
    expect(p.body.textContent).toBe('불러오지 못함');
    expect(errSpy).toHaveBeenCalled();
    expect(errorText(new AuthRequiredError())).toBe('로그인이 필요합니다');
    expect(errorText(new LockedError('k'))).toBe('로그인 등급 데이터');
    p.showMessage('아직 없음 — krx.daily 08:05');
    expect(p.body.textContent).toBe('아직 없음 — krx.daily 08:05');
  });

  it('자물쇠 문구: 키 또는 직접 쓴 문구', () => {
    expect(lockText('krx')).toBe(LOCK_REASONS.krx);
    expect(lockText('직접 쓴 사유')).toBe('직접 쓴 사유');
  });
});

describe('칩', () => {
  it('공개판의 로그인 칩은 "로그인 필요"(값을 싣지 않는다)', () => {
    const c = chip('public', { key: 'mkt', label: '시장 거래대금', value: '21.4조', tier: 'login', title: 'KRX' });
    expect(c.dataset.state).toBe('locked');
    expect(c.querySelector('.v')?.textContent).toBe('로그인 필요');
    expect(c.textContent).not.toContain('21.4조');
    expect(c.hasAttribute('title')).toBe(false);
  });

  it('잠정·마감·준비 중 꼬리표', () => {
    expect(chip('login', { key: 'a', label: '외국인', value: '+1,234억', state: 'estimated', cls: 'up' }).querySelector('.t')?.textContent).toBe('잠정');
    expect(chip('login', { key: 'a', label: '외국인', value: '+1', state: 'closed' }).querySelector('.t')?.textContent).toBe('마감');
    const p = chip('public', { key: 'gex', label: 'GEX Flip', state: 'pending', phase: 'P7' });
    expect(p.querySelector('.v')?.textContent).toBe('준비 중(P7)');
    expect(chip('login', { key: 'a', label: 'x', value: '1', cls: 'up' }).querySelector('.v')?.className).toBe('v up');
  });
});

describe('SVG', () => {
  it('svg 틀은 role=img + 이름', () => {
    const s = svgBox('0 0 10 10', '추이');
    expect(s.getAttribute('role')).toBe('img');
    expect(s.getAttribute('aria-label')).toBe('추이');
  });

  it('스파크라인: 빈 값은 건너뛴다', () => {
    const s = sparkline([1, null, 3, Number.NaN, 2], { label: '코스피' });
    const pts = s.querySelector('polyline')?.getAttribute('points')?.split(' ') ?? [];
    expect(pts).toHaveLength(3);
    expect(sparkline([], { label: 'x' }).querySelector('polyline')).toBeNull();
  });

  it('막대: 음수·빈 값은 그리지 않고 기준선', () => {
    const s = barChart([10, null, -1, 20], { label: '거래대금', ref: 15 });
    expect(s.querySelectorAll('rect')).toHaveLength(2);
    expect(s.querySelector('line')).not.toBeNull();
  });

  it('비율·수평 막대·히트맵 색', () => {
    expect(scale([2, 4])(3)).toBe(0.5);
    expect(scale([5, 5])(5)).toBe(0.5);
    const l = level('상승 비율', 140, '55:45');
    expect(l.querySelector<HTMLElement>('.bar span')?.style.width).toBe('100%');
    const c = centerLevel('외국인', -50, 100, '-50');
    const span = c.querySelector<HTMLElement>('.bar span');
    expect(span?.classList.contains('neg')).toBe(true);
    expect(span?.style.left).toBe('25%');
    expect(span?.style.width).toBe('25%');
    expect(heatColor(0)).toBe('var(--panel)');
    expect(heatColor(null)).toBe('var(--panel)');
    expect(heatColor(3)).toBe('color-mix(in srgb, var(--up) 60%, var(--panel))');
    expect(heatColor(-1.5)).toBe('color-mix(in srgb, var(--down) 38%, var(--panel))');
  });

  it('style 속성 거부', async () => {
    const { s } = await import('../src/ui/svg');
    expect(() => s('rect', { style: 'fill:red' })).toThrow('style');
  });
});
