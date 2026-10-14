// 페이지 12 ⑥ 상관관계 — 관심종목(main)/보유 × 60/120일 일간 수익률 피어슨 히트맵(docs/p8_design.md §4.7·§6, 묶음 JNL).
//
// - 값이 없는 칸(겹치는 날 < 40)은 '—'(0 으로 칠하지 않는다 — ui/heatmap). 종목 50개 상한(서버 notes).
// - 고르기(관심·보유·60·120)는 패널 안에서 다시 요청 — 실패는 이 차트 칸 안에서 끝난다.
// - 히트맵은 반폭 패널 안(전체 폭 차트 금지). 공개 빌드는 이 모듈을 번들에서 뺀다.
import type { components } from '../../api/types.gen';
import type { PageContext } from '../../app/types';
import { h } from '../../ui/dom';
import { heatmap } from '../../ui/heatmap';
import { errorText, type Panel } from '../../ui/panel';
import { seg } from '../../ui/seg';
import { isNoData, isNotice } from './journal';

export type Correlation = components['schemas']['Correlation'];
type SetName = Correlation['set'];
type Window = '60' | '120';

export const CORR_HINT = '아직 없음 — 상관을 볼 종목이 2개 미만';

export async function fillCorr(ctx: PageContext, p: Panel): Promise<void> {
  let set: SetName = 'watchlist';
  let n: Window = '60';
  const chart = h('div', { class: 'jr-corr' });
  const draw = async (): Promise<void> => {
    try {
      const env = await ctx.source.get<Correlation>('portfolio/correlation', {
        set,
        n,
      });
      const d = env.data;
      p.setSource(env);
      chart.replaceChildren(
        heatmap({
          label: `상관 행렬 ${set === 'watchlist' ? '관심종목' : '보유'} ${d.window_days}일`,
          labels: d.codes,
          names: d.names.map((x, i) => (x ? `${x} ${d.codes[i] ?? ''}` : null)),
          matrix: d.matrix,
        }),
      );
    } catch (err) {
      chart.replaceChildren(
        isNoData(err)
          ? h('p', { class: 'msg' }, err.serverMessage ?? CORR_HINT)
          : h('p', { class: isNotice(err) ? 'msg' : 'msg err' }, errorText(err)),
      );
    }
  };
  const sets = seg<SetName>({
    label: '대상',
    options: [
      { value: 'watchlist', label: '관심종목' },
      { value: 'holdings', label: '보유' },
    ],
    value: set,
    onChange: (v) => {
      set = v;
      void draw();
    },
  });
  const wins = seg<Window>({
    label: '기간',
    options: [
      { value: '60', label: '60일' },
      { value: '120', label: '120일' },
    ],
    value: n,
    onChange: (v) => {
      n = v;
      void draw();
    },
  });
  p.clearBody();
  p.body.append(h('div', { class: 'filters' }, sets.el, wins.el), chart);
  await draw();
}
