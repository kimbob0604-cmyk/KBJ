// P8 공용 부품(ui/form·heatmap — docs/p8_design.md D-P8-23)과 페이지 11·12 진입(패널 순서·칸 폭·자물쇠).
// 빈칸·잘못된 숫자는 0 이 아니라 null, 상관 값 없음은 빈 칸('—'), 범위 밖 값은 던진다, 좁은 폭(viewBox 만)을 본다.
import { describe, expect, it } from 'vitest';
import { DataError, type DataSource } from '../src/data/source';
import p11, { P11_PANELS, P11_SECTIONS } from '../src/pages/p11_rules';
import p12, { P12_PANELS, P12_SECTIONS } from '../src/pages/p12_journal';
import { choiceField, formErrors, numberField, rows, textField, toggle } from '../src/ui/form';
import { corrColor, heatmap, heatmapLayout, shortLabel } from '../src/ui/heatmap';
import { pageCtx, panelByTitle } from './pages/support';

const noSource: DataSource = {
  get: () => Promise.reject(new Error('요청하면 안 된다')),
} as unknown as DataSource;
// 웨이브 2 본문이 붙은 뒤(P8 WRAP): 로그인 진입은 데이터를 묻는다 — 빈 저장소(404 no_data)는 '.msg' 안내
const emptySource: DataSource = {
  tier: 'login',
  get: () => Promise.reject(new DataError('아직 없음', 404, '아직 없음 — 시험 빈 저장소')),
};

describe('form — 숫자 칸', () => {
  it('빈칸·범위 밖·정수 아님은 null(0 으로 바꾸지 않는다) + 칸 오류', () => {
    const f = numberField({ label: 'RSI 기간', min: 2, max: 50, integer: true, required: true });
    expect(f.value()).toBeNull();
    expect(f.el.querySelector('.form-err')?.textContent).toBe('필수');
    f.set(14);
    expect(f.value()).toBe(14);
    expect(f.input.getAttribute('aria-invalid')).toBe('false');
    for (const bad of ['1', '51', '2.5', 'abc']) {
      f.input.value = bad;
      expect(f.value(), bad).toBeNull();
      expect(f.input.getAttribute('aria-invalid')).toBe('true');
    }
  });

  it('입력 이벤트가 값(또는 null)을 알린다', () => {
    const seen: (number | null)[] = [];
    const f = numberField({ label: '값', onChange: (v) => seen.push(v) });
    f.input.value = '30';
    f.input.dispatchEvent(new Event('input'));
    f.input.value = '';
    f.input.dispatchEvent(new Event('input'));
    expect(seen).toEqual([30, null]);
  });
});

describe('form — 글자·선택·토글·행', () => {
  it('글자 칸은 앞뒤 공백을 뺀다, 선택 칸은 같은 칸 모양', () => {
    const t = textField({ label: '이름', value: '  규칙 ' });
    expect(t.value()).toBe('규칙');
    const c = choiceField({ label: '모드', options: [{ value: 'close', label: '마감' }, { value: 'intraday', label: '장중' }], value: 'close' });
    expect(c.el.classList.contains('form-field')).toBe(true);
    expect(c.value()).toBe('close');
  });

  it('토글은 role=switch·aria-checked·글자로 상태', () => {
    const seen: boolean[] = [];
    const t = toggle({ label: '켜짐', value: false, onChange: (v) => seen.push(v) });
    expect(t.el.getAttribute('role')).toBe('switch');
    expect(t.el.getAttribute('aria-checked')).toBe('false');
    t.el.click();
    expect(t.value()).toBe(true);
    expect(t.el.textContent).toContain('켜짐');
    expect(seen).toEqual([true]);
  });

  it('행 추가/삭제 — 최소·최대에서 버튼이 꺼진다', () => {
    const seen: number[] = [];
    const r = rows<number>({
      label: '조건',
      items: [1],
      make: () => 0,
      render: (v) => document.createTextNode(String(v)) as unknown as HTMLElement,
      min: 1,
      max: 2,
      onChange: (xs) => seen.push(xs.length),
    });
    const add = r.el.querySelector<HTMLButtonElement>('.rows-add');
    expect(r.el.querySelector<HTMLButtonElement>('.rows-del')?.disabled).toBe(true);
    add?.click();
    expect(r.items()).toEqual([1, 0]);
    expect(add?.disabled).toBe(true);
    r.el.querySelectorAll<HTMLButtonElement>('.rows-del')[0]?.click();
    expect(r.items()).toEqual([0]);
    expect(seen).toEqual([2, 1]);
  });

  it('서버 검증 오류 — 위치·사유만', () => {
    const el = document.createElement('div');
    formErrors(el, [{ pointer: '/when/all/0/op', reason: 'literal_error' }]);
    expect(el.getAttribute('role')).toBe('alert');
    expect(el.textContent).toContain('/when/all/0/op');
    expect(el.textContent).toContain('literal_error');
  });
});

describe('heatmap — 상관 행렬', () => {
  it('값 없음은 바탕색·— (0 으로 칠하지 않음), 범위 밖은 던진다', () => {
    expect(corrColor(null)).toBe('var(--panel)');
    expect(corrColor(Number.NaN)).toBe('var(--panel)');
    expect(corrColor(0.5)).toContain('--up');
    expect(corrColor(-0.5)).toContain('--down');
    expect(() => corrColor(1.2)).toThrow(RangeError);
    expect(() => heatmapLayout(['A', 'B'], [[1, 0.2]])).toThrow(RangeError);
  });

  it('칸 수 = n², 라벨 줄임, viewBox 만(폭 속성 없음), 칸 누르면 상세', () => {
    const picked: (number | null)[] = [];
    const el = heatmap({
      label: '상관',
      labels: ['005930', '000660', 'LONGNAME01'],
      names: ['삼성전자', 'SK하이닉스', null],
      matrix: [
        [1, 0.8, null],
        [0.8, 1, -0.3],
        [null, -0.3, 1],
      ],
      onCell: (_r, _c, v) => picked.push(v),
    });
    const svg = el.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBeTruthy();
    expect(svg?.hasAttribute('width')).toBe(false);
    expect(el.querySelectorAll('.heat-cell')).toHaveLength(9);
    expect(shortLabel('LONGNAME01')).toBe('LONGN…');
    expect(el.textContent).toContain('LONGN…');
    const cells = el.querySelectorAll<SVGGElement>('.heat-cell');
    cells[1]?.dispatchEvent(new Event('click'));
    expect(el.querySelector('.heatmap-detail')?.textContent).toContain('삼성전자 × SK하이닉스');
    cells[2]?.dispatchEvent(new Event('click'));
    expect(picked).toEqual([0.8, null]);
    expect(el.querySelector('.heatmap-detail')?.textContent).toContain('—');
  });
});

describe('페이지 11·12 진입 — 패널 순서·칸 폭(차트는 반폭 이하)', () => {
  it('공개: 패널 전부 자물쇠, 요청 0', async () => {
    for (const [mod, sections, panels, n] of [
      [p11, P11_SECTIONS, P11_PANELS, 11],
      [p12, P12_SECTIONS, P12_PANELS, 12],
    ] as const) {
      const { ctx, root } = pageCtx(n, 'public', noSource);
      await mod.mount(ctx);
      const titles = [...root.querySelectorAll('section.panel h3 > span:first-child')].map((s) => s.textContent);
      expect(titles).toEqual(sections.map((id) => (panels as Record<string, { title: string }>)[id]?.title));
      expect(root.querySelectorAll('section.panel.locked')).toHaveLength(sections.length);
    }
  });

  it('로그인: 자물쇠 없이 준비 중 안내(.msg — 오류 아님), 차트 칸(성과 분포·누적 곡선·상관)은 span-6', async () => {
    const { ctx, root } = pageCtx(11, 'login', emptySource);
    await p11.mount(ctx);
    expect(root.querySelectorAll('section.panel.locked')).toHaveLength(0);
    expect(root.querySelectorAll('.msg.err')).toHaveLength(0);
    expect(panelByTitle(root, '성과 분포').classList.contains('span-6')).toBe(true);
    expect(panelByTitle(root, '규칙').classList.contains('span-12')).toBe(true);
    const r12 = pageCtx(12, 'login', emptySource);
    await p12.mount(r12.ctx);
    for (const t of ['누적 곡선', '상관관계']) {
      expect(panelByTitle(r12.root, t).classList.contains('span-6'), t).toBe(true);
    }
    expect(r12.root.querySelectorAll('.msg:not(.err)')).toHaveLength(P12_SECTIONS.length);
  });
});
