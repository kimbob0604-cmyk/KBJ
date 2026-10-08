// 공개 빌드 부팅 — 정적 데이터(./data/*.json) + TradingView. 로그인 코드(data/api·data/auth)를 import 하지 않는다.
import { type AppHandle, type MountOptions, mountApp } from '../app/app';
import { createStaticSource, type StaticSourceOptions } from '../data/static';
import { mountTradingView } from '../pages/tradingview';

export interface BootPublicOptions extends StaticSourceOptions, Partial<Omit<MountOptions, 'tier' | 'source'>> {}

export function bootPublic(root: HTMLElement, o: BootPublicOptions = {}): AppHandle {
  const { fetch, base, ...rest } = o;
  const source = createStaticSource({ ...(fetch ? { fetch } : {}), ...(base ? { base } : {}) });
  return mountApp(root, { tradingview: mountTradingView, ...rest, tier: 'public', source });
}
