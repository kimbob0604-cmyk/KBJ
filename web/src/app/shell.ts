// 상단 바 — 브랜드·KST 시계(1초)·등급 배지·스킨 선택·(로그인 빌드) 로그아웃(§6.4).
// 시계는 시각만 보인다. 장 세션·카운트다운은 거래 캘린더가 정본이라 상단 띠(calendar.json·/ribbon)에서 그린다.
import type { Tier } from '../data/source';
import { h } from '../ui/dom';
import { kstClock } from '../ui/fmt';
import { seg } from '../ui/seg';
import { applySkin, loadSkin, saveSkin, SKINS, type SkinId } from './skin';
import type { Cleanup } from './types';

export interface TopBarOptions {
  tier: Tier;
  now: () => Date;
  every: (ms: number, fn: () => void, opts?: { immediate?: boolean }) => Cleanup;
  /** 로그인 빌드: 로그인한 이름(화면에는 이름만) */
  user?: string;
  onLogout?: () => void;
  storage?: Pick<Storage, 'getItem' | 'setItem'> | null;
}

export const TIER_BADGE: Record<Tier, string> = { public: '공개판', login: '로그인' };

export function createTopBar(o: TopBarOptions): HTMLElement {
  const clock = h('div', { class: 'clock', attrs: { 'aria-live': 'off' } });
  const tick = (): void => {
    clock.textContent = `KST ${kstClock(o.now())}`;
  };
  tick();
  o.every(1000, tick);

  const initial = loadSkin(o.storage);
  applySkin(initial);
  const skins = seg<SkinId>({
    label: '스킨',
    options: SKINS.map((s) => ({ value: s.id, label: s.label })),
    value: initial,
    onChange: (v) => {
      applySkin(v);
      saveSkin(v, o.storage);
    },
  });

  const right: HTMLElement[] = [skins.el];
  if (o.tier === 'login' && o.onLogout) {
    const onLogout = o.onLogout;
    right.push(h('button', { class: 'btn', attrs: { type: 'button' }, on: { click: () => onLogout() } }, '로그아웃'));
  }

  return h(
    'header',
    { class: 'top' },
    h('h1', { class: 'brand' }, 'KBJ', h('small', null, '국장 터미널')),
    clock,
    h(
      'span',
      { class: 'badge', attrs: { title: o.tier === 'public' ? '공개 등급 데이터만' : '로그인 등급 포함' } },
      TIER_BADGE[o.tier],
      o.user ? ` · ${o.user}` : '',
    ),
    h('div', { class: 'spacer' }),
    right,
  );
}
