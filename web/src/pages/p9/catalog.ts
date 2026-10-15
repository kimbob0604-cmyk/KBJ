// 페이지 9 대테마 분류(공개 — ADR 0039). 섹션별 대테마 목록·밸류체인 묶음. 두 빌드가 같이 쓴다.
//
// - 키 'themes': 공개판 = 공개 파일 themes.json(public.export — pub_themes 만), 로그인판 = API(같은 모양).
//   이 파일에는 API 경로 문자열이 없다(공개 번들 검사 — check-bundle.mjs).
// - 대테마 이름은 사용자가 만든 분류. 네이버 유래(원본 테마·편입 종목·사유)·시세 기반 강도는 없다(로그인 — p9/strength.ts).
// - 404(수집 전)는 '.msg' 안내(작업·시각), 그 밖 실패만 '.msg.err'(p1_market failed).
import type { components } from '../../api/types.gen';
import type { DataSource, Envelope, Tier } from '../../data/source';
import { h } from '../../ui/dom';
import { num } from '../../ui/fmt';
import { type Panel, panel } from '../../ui/panel';
import { failed } from '../p1_market';

export type ThemeCatalog = components['schemas']['ThemeCatalog'];
export type ThemeInfo = ThemeCatalog['sections'][number]['themes'][number];

export const THEME_SOURCE = 'stock-dashboard 테마 마스터';
export const HINT_CLASSES = 'theme.classes_sync 일 12:40(테마 마스터 주 1회)';

export interface P9Ctx {
  readonly tier: Tier;
  readonly source: DataSource;
  readonly now: () => Date;
  /** 대테마를 골랐다(로그인판 — 강도·편입 패널이 그 테마로) */
  readonly pick?: (id: string) => void;
}

export const CATALOG_PANELS = {
  sections: { title: '대테마 분류', span: 8 },
  chains: { title: '밸류체인', span: 4 },
} as const;

/** 공개 패널 둘(분류·밸류체인)을 만든다 — 그리기는 fillCatalog */
export function catalogPanels(tier: Tier): { sections: Panel; chains: Panel } {
  return {
    sections: panel(tier, {
      title: CATALOG_PANELS.sections.title,
      tier: 'public',
      span: CATALOG_PANELS.sections.span,
      src: THEME_SOURCE,
      def: '사용자가 만든 대테마 분류(섹션별). 테마 강도·편입 종목은 로그인',
    }),
    chains: panel(tier, {
      title: CATALOG_PANELS.chains.title,
      tier: 'public',
      span: CATALOG_PANELS.chains.span,
      src: THEME_SOURCE,
      def: '대테마의 밸류체인 값으로 묶음',
    }),
  };
}

function themeChip(t: ThemeInfo, pick?: (id: string) => void): HTMLElement {
  if (!pick) return h('span', { class: 'tag', data: { theme: t.id } }, t.name);
  return h(
    'button',
    { class: 'btn tag', attrs: { type: 'button' }, data: { theme: t.id }, on: { click: () => pick(t.id) } },
    t.name,
  );
}

export function drawCatalog(env: Envelope<ThemeCatalog>, p: { sections: Panel; chains: Panel }, pick?: (id: string) => void): void {
  const d = env.data;
  const names = new Map<string, ThemeInfo>();
  p.sections.clearBody();
  for (const s of d.sections) {
    for (const t of s.themes) names.set(t.id, t);
    p.sections.body.append(
      h(
        'div',
        { class: 'theme-section', data: { section: s.section } },
        h('h4', null, `${s.section} `, h('small', null, `${num(s.themes.length)}개`)),
        h('div', { class: 'theme-chips' }, ...s.themes.map((t) => themeChip(t, pick))),
      ),
    );
  }
  if (!d.sections.length) p.sections.showMessage('대테마 없음');
  p.sections.setSource(env);
  p.chains.clearBody();
  if (!d.valuechains.length) p.chains.showMessage('밸류체인이 적힌 대테마 없음');
  for (const c of d.valuechains) {
    p.chains.body.append(
      h(
        'div',
        { class: 'theme-chain', data: { chain: c.key } },
        h('h4', null, c.key),
        h(
          'div',
          { class: 'theme-chips' },
          ...c.themes.map((id) => {
            const t = names.get(id);
            return t ? themeChip(t, pick) : h('span', { class: 'tag dim' }, id);
          }),
        ),
      ),
    );
  }
  p.chains.setSource(env);
}

/** 분류를 받아 두 패널에 그린다. 받은 봉투를 돌려준다(실패면 null — 패널에 안내·오류 한 줄) */
export async function fillCatalog(t: P9Ctx, p: { sections: Panel; chains: Panel }): Promise<Envelope<ThemeCatalog> | null> {
  let env: Envelope<ThemeCatalog>;
  try {
    env = await t.source.get<ThemeCatalog>('themes');
  } catch (err) {
    failed(p.sections, err, HINT_CLASSES);
    failed(p.chains, err, HINT_CLASSES);
    return null;
  }
  drawCatalog(env, p, t.pick);
  return env;
}
