// 품질 표시 — 네 품질, invalid 미표시·수 표시, 검산 불가 문구 (§6.7·§6.8)
import { describe, expect, it } from 'vitest';
import { worstQuality } from '../src/data/source';
import {
  checkLine,
  emptyText,
  markQuality,
  notesLine,
  QUALITY_LABEL,
  qValue,
  sourceText,
  visibleRows,
} from '../src/ui/quality';

describe('값 품질 표시', () => {
  it('ok: 꾸밈 없음', () => {
    const el = qValue('1,234억', 'ok');
    expect(el?.textContent).toBe('1,234억');
    expect(el?.className).toBe('');
  });

  it('estimated: 잠정 배지 + 점선 밑줄 클래스', () => {
    const el = qValue('1,234억', 'estimated');
    expect(el?.classList.contains('q-est')).toBe(true);
    expect(el?.querySelector('.q-badge')?.textContent).toBe('잠정');
  });

  it('stale: 흐린 글자 + 기준 HH:MM(KST)', () => {
    const el = qValue('1,234억', 'stale', '2026-10-07T05:10:00Z');
    expect(el?.classList.contains('q-stale')).toBe(true);
    expect(el?.querySelector('.q-badge.stale')?.textContent).toBe('기준 14:10');
  });

  it('invalid: 그리지 않는다', () => {
    expect(qValue('1,234억', 'invalid')).toBeNull();
    const span = document.createElement('span');
    expect(markQuality(span, 'invalid')).toBe(false);
  });

  it('네 품질 이름', () => {
    expect(QUALITY_LABEL).toEqual({ ok: '확정', stale: '지연', estimated: '잠정', invalid: '검산 실패' });
  });
});

describe('행·메모·원천 줄', () => {
  it('invalid 행은 빼고 수를 센다', () => {
    const r = visibleRows([
      { code: 'A', quality: 'ok' as const },
      { code: 'B', quality: 'invalid' as const },
      { code: 'C', quality: 'estimated' as const },
      { code: 'D', quality: 'invalid' as const },
    ]);
    expect(r.rows.map((x) => x.code)).toEqual(['A', 'C']);
    expect(r.invalid).toBe(2);
  });

  it('메모 줄: notes + 검산 실패 n행 제외', () => {
    expect(notesLine(['NXT 미포함'], 3)?.textContent).toBe('NXT 미포함 · 검산 실패 3행 제외');
    expect(notesLine([], 0)).toBeNull();
  });

  it('원천 줄: 원천 · KST 시각 · (ok 가 아니면) 품질', () => {
    expect(sourceText({ source: 'KRX', as_of: '2026-10-07T06:30:00Z', quality: 'ok' })).toBe('KRX · 10/07 15:30');
    expect(sourceText({ source: 'KIS(잠정)', as_of: '2026-10-07T01:10:00Z', quality: 'estimated' })).toBe(
      'KIS(잠정) · 10/07 10:10 · 잠정',
    );
  });

  it('봉투 품질 = 가장 나쁜 것', () => {
    expect(worstQuality(['ok', 'estimated', 'stale'])).toBe('estimated');
    expect(worstQuality([])).toBe('ok');
    expect(worstQuality(['ok', 'invalid'])).toBe('invalid');
  });

  it('빈 데이터 문구', () => {
    expect(emptyText('krx.daily', '08:05')).toBe('아직 없음 — krx.daily 08:05');
    expect(emptyText()).toBe('아직 없음');
  });
});

describe('검산 줄', () => {
  it('잔차 0 → 정상', () => {
    const el = checkLine('4구분 합', 0, { unit: '원' });
    expect(el.dataset.check).toBe('ok');
    expect(el.textContent).toBe('4구분 합 차이 0원');
  });

  it('잔차가 있으면 경고', () => {
    const el = checkLine('4구분 합', 1200);
    expect(el.dataset.check).toBe('fail');
    expect(el.classList.contains('fail')).toBe(true);
    expect(el.textContent).toContain('+1,200');
    expect(el.textContent).toContain('0 이어야 정상');
  });

  it('검산 불가: 사유를 쓰고 0 으로 그리지 않는다', () => {
    const el = checkLine('4구분 합', null, { reason: '기타법인 미제공' });
    expect(el.dataset.check).toBe('unavailable');
    expect(el.textContent).toBe('검산 불가: 기타법인 미제공');
    expect(el.textContent).not.toContain('0');
  });
});
