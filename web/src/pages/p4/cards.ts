// 페이지 4 스크리너 요약 카드(2026-11-02 가시성 개선 — 주식봇식). 로그인 본문만 쓴다(공개 번들에서 빠진다).
// 표에 보이는 종목 안에서 외국인·기관·개인 순매수 1위와 거래대금 폭증 1위.
import type { components } from '../../api/types.gen';
import { joEok, num, pct, times } from '../../ui/fmt';
import type { StatCard } from '../../ui/stat';

type Row = components['schemas']['ScreenRow'];

function topBy(rows: readonly Row[], get: (r: Row) => number | null): Row | undefined {
  let best: Row | undefined;
  for (const r of rows) {
    const v = get(r);
    if (v !== null && v > 0 && (best === undefined || v > (get(best) ?? 0))) best = r;
  }
  return best;
}

export function screenCards(rows: readonly Row[], period: string): StatCard[] {
  const who = (label: string, get: (r: Row) => number | null, streak?: (r: Row) => number | null): StatCard => {
    const r = topBy(rows, get);
    const s = r && streak ? streak(r) : null;
    return {
      label: `${label} 순매수 1위`,
      asof: period,
      value: r ? (r.name ?? r.code) : null,
      tone: r ? 'up' : null,
      sub: r
        ? `${joEok(get(r), true)}${s ? ` · ${num(s)}일 연속` : ''} · ${pct(r.chg_pct)}`
        : '표 안에 순매수 종목 없음',
      subTone: r ? null : 'dim',
    };
  };
  const spike = topBy(rows, (r) => r.spike_mult);
  return [
    who(
      '외국인',
      (r) => r.foreign,
      (r) => r.streak_foreign,
    ),
    who(
      '기관',
      (r) => r.inst,
      (r) => r.streak_inst,
    ),
    who('개인', (r) => r.indiv),
    {
      label: '거래대금 폭증 1위',
      value: spike ? (spike.name ?? spike.code) : null,
      tone: spike ? 'warn' : null,
      sub: spike ? `평소의 ${times(spike.spike_mult, 1)} · ${pct(spike.chg_pct)}` : null,
      title: '그날 거래대금 ÷ 직전 20일 평균',
    },
  ];
}
