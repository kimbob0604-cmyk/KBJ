// fmt — 원 → 억·조 반올림은 화면에서만, 부호, KST 표시(UTC 입력) (§6.8)
import { describe, expect, it } from 'vitest';
import {
  DASH,
  eok,
  eokNum,
  joEok,
  kstClock,
  kstDate,
  kstParts,
  kstStamp,
  kstTime,
  num,
  pct,
  shortDate,
  signClass,
  times,
  toDate,
} from '../src/ui/fmt';

describe('숫자', () => {
  it('세 자리 묶음과 소수 자릿수', () => {
    expect(num(1234567)).toBe('1,234,567');
    expect(num(1234.567, 2)).toBe('1,234.57');
    expect(num(-1234.5, 0)).toBe('-1,235');
    expect(num(999.995, 2)).toBe('1,000.00');
  });

  it('부호: 양수 +, 반올림해 0 이면 부호 없음(-0 금지)', () => {
    expect(num(5, 0, true)).toBe('+5');
    expect(num(-0.004, 2, true)).toBe('0.00');
    expect(num(0.004, 2, true)).toBe('0.00');
    expect(num(-0, 0, true)).toBe('0');
    expect(pct(1.234)).toBe('+1.23%');
    expect(pct(-0.5, 1)).toBe('-0.5%');
    expect(pct(0)).toBe('0.00%');
    expect(times(1.5)).toBe('1.50x');
  });

  it('값이 없으면 — (0 으로 그리지 않는다)', () => {
    for (const v of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(num(v)).toBe(DASH);
      expect(pct(v)).toBe(DASH);
      expect(eok(v)).toBe(DASH);
      expect(joEok(v)).toBe(DASH);
    }
  });

  it('원 → 억(정수 반올림)', () => {
    expect(eok(123_456_789_012)).toBe('1,235억');
    expect(eok(149_999_999)).toBe('1억');
    expect(eok(150_000_000)).toBe('2억');
    expect(eok(-3_049_000_000, 0, true)).toBe('-30억');
    expect(eok(3_051_000_000, 0, true)).toBe('+31억');
    expect(eokNum(30_000_000_000)).toBe('300');
    expect(eok(25_000_000, 1)).toBe('0.3억');
  });

  it('1조 이상은 조(소수 1자리), 아래는 억', () => {
    expect(joEok(17_400_000_000_000)).toBe('17.4조');
    expect(joEok(1_000_000_000_000)).toBe('1.0조');
    expect(joEok(999_949_999_999)).toBe('9,999억');
    expect(joEok(999_950_000_000)).toBe('1.0조'); // 억 반올림이 10,000억이 되는 경계
    expect(joEok(-999_960_000_000, true)).toBe('-1.0조');
    expect(joEok(-2_345_000_000_000, true)).toBe('-2.3조');
  });

  it('부호 색: 상승 up(적색)·하락 down(청색)·0/없음 dim', () => {
    expect(signClass(1)).toBe('up');
    expect(signClass(-1)).toBe('down');
    expect(signClass(0)).toBe('dim');
    expect(signClass(null)).toBe('dim');
  });
});

describe('KST 시각', () => {
  it('UTC 입력을 KST 로(브라우저 시간대와 무관)', () => {
    expect(kstClock('2026-10-07T00:00:00Z')).toBe('09:00:00');
    expect(kstTime('2026-10-07T06:30:00Z')).toBe('15:30');
    expect(kstStamp('2026-10-07T15:30:00Z')).toBe('10/08 00:30');
    expect(kstDate('2026-10-06T15:00:00Z')).toBe('2026-10-07');
    expect(kstDate('2026-10-06T14:59:59Z')).toBe('2026-10-06');
  });

  it('오프셋 있는 입력', () => {
    expect(kstTime('2026-10-07T15:30:00+09:00')).toBe('15:30');
    expect(kstTime('2026-10-07T02:30:00-04:00')).toBe('15:30');
  });

  it('요일(KST 기준)', () => {
    // 2026-10-07 수요일. UTC 로는 10-06 화요일 20:00 이지만 KST 는 10-07 05:00
    expect(kstParts('2026-10-06T20:00:00Z')?.weekday).toBe(3);
  });

  it('시간대 없는 시각(naive)은 거부 — 뜻이 모호하다', () => {
    expect(toDate('2026-10-07T15:30:00')).toBeNull();
    expect(kstTime('2026-10-07T15:30:00')).toBe(DASH);
    expect(kstTime('잘못된 값')).toBe(DASH);
    expect(kstTime(null)).toBe(DASH);
  });

  it('날짜만 있는 입력은 변환하지 않는다', () => {
    expect(kstDate('2026-10-07')).toBe('2026-10-07');
    expect(shortDate('2026-10-07')).toBe('10/07');
    expect(shortDate(undefined)).toBe(DASH);
  });

  it('Date 입력', () => {
    expect(kstClock(new Date(Date.UTC(2026, 9, 7, 1, 23, 45)))).toBe('10:23:45');
  });
});
