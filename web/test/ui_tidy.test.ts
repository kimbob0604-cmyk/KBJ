// 화면 문구 정리(ui/tidy) — 개발용 표시(작업 이름·실측 번호·준비 중)를 사용자 문장으로.
import { describe, expect, it } from 'vitest';
import { NOT_PROVIDED, tidy, tidyDom, tidyNotes } from '../src/ui/tidy';
import { flush } from './pages/support';

describe('tidy', () => {
  it('작업 이름·실측 꼬리표를 지우고 시각은 남긴다', () => {
    expect(tidy('아직 없음 — market.close_collect 15:35(시장 투자자)')).toBe('아직 없음 — 15:35(시장 투자자)');
    expect(tidy('아직 없음 — macro.morning 06:20 · macro.monthly 09:10 (확인 전 #43~#46)')).toBe(
      '아직 없음 — 06:20 · 09:10',
    );
    expect(tidy('지수 2001 자료 없음')).toBe('지수 2001 자료 없음');
  });

  it('꺼 둔 작업·개발 단계 문구는 통째로 한 줄', () => {
    expect(tidy('아직 없음 — program.daily 15:50(확인 전 #40)')).toBe(NOT_PROVIDED);
    expect(tidy('확인 전 — 이 시장 조합은 실측(#39) 뒤에 켠다.')).toBe(NOT_PROVIDED);
  });

  it('[확인 필요 — 설명] 은 설명만 남긴다', () => {
    expect(tidy('월별 수익률 분모 = 월초 원가 [확인 필요 — 현금 원장 없음]')).toBe(
      '월별 수익률 분모 = 월초 원가 (현금 원장 없음)',
    );
    expect(tidy('코스닥 조합 코드 [확인 필요 #39]')).toBe('코스닥 조합 코드');
  });

  it('주석 줄은 개발 단계 줄을 빼고 나머지를 남긴다', () => {
    expect(tidyNotes(['확인 전 — 코스닥 조합 코드 [확인 필요 #39]', '분 단위 선', '분 단위 선'])).toEqual(['분 단위 선']);
  });

  it('tidyDom 은 나중에 그려진 안내 줄도 고친다', async () => {
    const root = document.createElement('div');
    const stop = tidyDom(root);
    const p = document.createElement('p');
    p.className = 'msg';
    p.textContent = '아직 없음 — board.daily 16:00~16:05';
    root.append(p);
    await flush();
    expect(p.textContent).toBe('아직 없음 — 16:00~16:05');
    stop();
  });
});
