// 공개 사이트 부팅 — Beom's Dashboard 방식(사용자 결정 2026-11-09, docs/adr/0042-site-snapshot.md).
// 로그인 화면과 같은 위젯(등급 'login' — 자물쇠 없음)을 맥이 올린 정적 JSON(data/snapshot.ts)으로 그린다.
// 로그인·쓰기 없음. 개인 기능 페이지(11 알림·규칙·12 백테스트·저널·13 운영)는 붙이지 않는다.
import { type AppHandle, type MountOptions, mountApp } from '../app/app';
import { createSnapshotSource, type SnapshotSourceOptions } from '../data/snapshot';

/** 공개 사이트에 붙이는 페이지 — 1~10(개인 기능 11·12·13 제외) */
export const SITE_PAGES: readonly number[] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

export const SITE_FOOT =
  '맥이 모은 데이터를 장중 10분마다 올린 화면입니다(맥이 꺼져 있으면 마지막으로 올린 화면). 숫자마다 원천·시각·품질을 붙입니다. 투자 판단의 근거로 쓰지 마세요.';

export interface BootSiteOptions
  extends SnapshotSourceOptions,
    Partial<Omit<MountOptions, 'tier' | 'source' | 'user' | 'onLogout'>> {}

export function bootSite(root: HTMLElement, o: BootSiteOptions = {}): AppHandle {
  const { fetch, base, ...rest } = o;
  const source = createSnapshotSource({ ...(fetch ? { fetch } : {}), ...(base ? { base } : {}) });
  return mountApp(root, {
    pages: SITE_PAGES,
    foot: SITE_FOOT,
    badge: { text: '스냅샷', title: '맥이 올린 정적 화면 — 로그인 없음' },
    ...rest,
    tier: 'login',
    source,
  });
}
