// 장중 슬롯 맞춤 새로 고침(app/refresh.ts onSlot — 2026-10-27 실시간 점검, docs/realtime_audit.md).
import { describe, expect, it } from 'vitest';
import { onSlot, SLOT_OFFSET_MS, SLOT_PERIOD_MS } from '../src/app/refresh';

describe('onSlot — 벽시계 경계에서만 부른다', () => {
  it('만든 구간은 건너뛰고, 경계(슬롯 + 2분 30초·+ 7분 30초)를 넘을 때 한 번', () => {
    let t = Date.parse('2026-10-27T00:59:00Z'); // 09:59 KST
    let n = 0;
    const tick = onSlot(
      () => new Date(t),
      () => (n += 1),
    );
    tick();
    expect(n).toBe(0);
    t = Date.parse('2026-10-27T01:02:29Z'); // 10:02:29 — 아직 09:57:30 구간
    tick();
    expect(n).toBe(0);
    t = Date.parse('2026-10-27T01:02:30Z'); // 10:02:30
    tick();
    tick();
    expect(n).toBe(1);
    t = Date.parse('2026-10-27T01:07:30Z');
    tick();
    expect(n).toBe(2);
  });

  it('탭이 숨은 사이 여러 경계를 넘어도 다시 보일 때 한 번만', () => {
    let t = 0;
    let n = 0;
    const tick = onSlot(
      () => new Date(t),
      () => (n += 1),
    );
    t += SLOT_PERIOD_MS * 7 + SLOT_OFFSET_MS;
    tick();
    tick();
    expect(n).toBe(1);
  });

  it('주기·여유를 바꿀 수 있다(페이지 11 발송 이력 10분)', () => {
    let t = Date.parse('2026-10-27T01:03:00Z'); // 10:03 KST — 10:02:30 구간
    let n = 0;
    const tick = onSlot(
      () => new Date(t),
      () => (n += 1),
      600_000,
    );
    t = Date.parse('2026-10-27T01:07:30Z');
    tick();
    expect(n).toBe(0); // 10분 주기는 10:12:30 까지 같은 구간
    t = Date.parse('2026-10-27T01:12:30Z');
    tick();
    expect(n).toBe(1);
  });
});
