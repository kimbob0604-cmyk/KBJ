// 페이지 6 시리즈 패널 — 공개 탭 6(금리·통화 / 물가 / 성장·경기 / 고용·가계 / 대외·환율 / 미국)을 탭으로(docs/p5_design.md §6).
// '시장' 탭(로그인 등급 — 저작권·작성기관 제한)은 따로 market 패널. 탭은 처음 보일 때 한 번 불러온다(ui/tabs onFirstShow).
// 한 탭의 실패·'아직 없음'은 그 탭 안 한 줄(다른 탭은 계속 — 절대 규칙 4).
import type { Envelope } from '../../data/source';
import { append, h } from '../../ui/dom';
import { kstStamp } from '../../ui/fmt';
import { errorText, panel, type Panel } from '../../ui/panel';
import { sourceText } from '../../ui/quality';
import { tabs } from '../../ui/tabs';
import { MACRO_HINT, type MacroSeriesSet, type MacroTab, type P6Ctx, seriesGrid, seriesKey } from './common';

export const SERIES_TITLE = '매크로 지표';
/** 보고서 지표 묶음(사용자 결정 Q2 — 원본 xlsx 대신 인용 ECOS 시리즈 + 원문 링크) */
export const BOK_REPORT_URL = 'https://www.bok.or.kr/portal/bbs/B0000156/list.do?menuNo=200761';

export async function loadTab(c: P6Ctx, tab: string, into: HTMLElement): Promise<Envelope<MacroSeriesSet> | null> {
  const { key, params } = seriesKey(c.tier, tab);
  try {
    const env = await c.source.get<MacroSeriesSet>(key, params);
    append(into, [
      h('p', { class: 'src' }, sourceText(env)),
      seriesGrid(env.data.series),
      env.notes.length ? h('p', { class: 'notes' }, env.notes.join(' · ')) : null,
    ]);
    return env;
  } catch (err) {
    into.append(h('p', { class: 'msg err' }, `${errorText(err)} — ${MACRO_HINT}`));
    return null;
  }
}

export function createSeriesPanel(c: P6Ctx, publicTabs: readonly MacroTab[]): { panel: Panel; settled: () => Promise<void> } {
  const p = panel(c.tier, { title: SERIES_TITLE, tier: 'public', span: 8 });
  p.setDef('카드 = 최신값·직전 공표 대비 변화·기준 기간·받은 시각·출처. 확인 전 시리즈는 값 없이 실측 번호.');
  if (publicTabs.length === 0) {
    p.showMessage(`아직 없음 — ${MACRO_HINT}`);
    return { panel: p, settled: () => Promise.resolve() };
  }
  const t = tabs<string>({
    label: '매크로 탭',
    tabs: publicTabs.map((x) => ({ id: x.id, label: x.title })),
    onFirstShow: (id, el) => loadTab(c, id, el),
  });
  p.body.append(
    t.el,
    h(
      'p',
      { class: 'def' },
      '보고서 지표: 한국은행 통화신용정책보고서가 인용하는 ECOS 시리즈(금리·통화 탭) — 원문은 ',
      h('a', { attrs: { href: BOK_REPORT_URL, target: '_blank', rel: 'noopener noreferrer' } }, '한국은행 누리집'),
      `(${kstStamp(c.now())} 기준 화면)`,
    ),
  );
  return { panel: p, settled: () => t.settled() };
}
