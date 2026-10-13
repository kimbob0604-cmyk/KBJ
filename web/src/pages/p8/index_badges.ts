// 페이지 8 머리 — 지수 정기변경 배지 한 줄(P5 2차 묶음 IDX — 예 'MSCI Korea 편입 후보 · 필요 +12.4%'). 로그인 전용:
// p8_stock.ts 가 MODE 'public' 이면 이 모듈을 번들에서 뺀다(구성종목·컷오프 추정 — 공개판에 없다).
//
// 'index-review/stock/<코드>' 의 badges(편입·편출·경계 후보만 — 유지는 서버가 빼서 온다). 배지가 없으면 줄을 그리지 않는다.
// 엔진 추정 컷오프 배지는 '추정', 사용자 입력 컷오프 배지는 꼬리표 '(사용자 컷오프)'. 실패는 머리를 지우지 않고 한 줄로.
import type { components } from '../../api/types.gen';
import { DataError } from '../../data/source';
import { h } from '../../ui/dom';
import { errorText, type Panel } from '../../ui/panel';
import { estBadge } from '../../ui/quality';
import { tag, type TabCtx } from './common';

type S = components['schemas'];
export type Badges = S['StockIndexBadges'];
type Badge = S['StockIndexBadge'];

const VERDICT_CLS: Record<Badge['verdict'], string | undefined> = {
  add: 'up',
  delete: 'warn',
  borderline: 'dim',
  keep: 'dim',
};

export function badgeEl(b: Badge): HTMLSpanElement {
  const text = `${b.label}${b.cutoff_kind === 'user' ? ' (사용자 컷오프)' : ''}`;
  const el = tag(text, VERDICT_CLS[b.verdict], `${b.index_title} ${b.review_id} 리뷰 — 컷오프 ${b.cutoff_kind === 'user' ? '사용자 입력' : '엔진 추정'}`);
  el.dataset.index = b.index_id;
  if (b.quality === 'estimated') el.append(' ', estBadge());
  return el;
}

/** 머리 패널 본문 끝에 배지 한 줄을 붙인다(배지 없으면 아무것도). */
export async function loadIndexBadges(p: Panel, t: Pick<TabCtx, 'code' | 'get'>): Promise<void> {
  try {
    const env = await t.get<Badges>(`index-review/stock/${t.code}`);
    if (env.data.code !== t.code || !env.data.badges.length) return;
    p.body.append(h('p', { class: 'p8-title', data: { section: 'index-badges' } }, '지수 정기변경 ', env.data.badges.map(badgeEl)));
  } catch (err) {
    if (err instanceof DataError && err.status === 404) return; // 예측·일정 아직 없음 — 배지 없음
    p.body.append(h('p', { class: 'msg', data: { section: 'index-badges' } }, `지수 정기변경 배지: ${errorText(err)}`));
  }
}
