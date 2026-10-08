// TradingView 위젯(임베드) — **공개 빌드에만** 들어간다(DATA_TIERS §2 "공개판에서 시세는 TradingView 위젯으로만").
// 데이터는 TradingView 가 자기 라이선스로 표시한다 — 우리는 재배포하지 않는다. boot/public.ts 만 이 파일을 import 하고
// 페이지 모듈은 ctx.tradingview 로 받는다(로그인 번들에 TradingView 주소 0 — scripts/check-bundle.mjs).
//
// 위젯 방식: <script src=".../embed-widget-<종류>.js"> 의 글자(JSON)가 설정. 공개판 CSP 는 이 호스트만 연다(vite.config.ts).
// KRX 심볼·히트맵 지원 범위는 [확인 필요](p3_design R14) — 안 되면 페이지가 자물쇠로 바꾼다.
import { h } from '../ui/dom';

export const TV_SCRIPT_BASE = 'https://s3.tradingview.com/external-embedding/embed-widget-';

export type TvWidget = 'ticker-tape' | 'mini-symbol-overview' | 'market-overview' | 'symbol-overview' | 'stock-heatmap';

/** 심볼 [확인 필요 — R14: TradingView 의 KRX 지수 심볼 이름] */
export const TV_SYMBOLS = {
  kospi: 'KRX:KOSPI',
  kosdaq: 'KRX:KOSDAQ',
  kospi200: 'KRX:KOSPI200',
} as const;

export type TradingViewMount = (
  container: HTMLElement,
  widget: TvWidget,
  config: Readonly<Record<string, unknown>>,
) => () => void;

function colorTheme(doc: Document): 'light' | 'dark' {
  return doc.documentElement.dataset.skin === 'white' ? 'light' : 'dark';
}

export const mountTradingView: TradingViewMount = (container, widget, config) => {
  const doc = container.ownerDocument;
  const script = doc.createElement('script');
  script.src = `${TV_SCRIPT_BASE}${widget}.js`;
  script.async = true;
  script.textContent = JSON.stringify({ colorTheme: colorTheme(doc), isTransparent: true, locale: 'kr', ...config });
  const box = h(
    'div',
    { class: 'tradingview-widget-container', data: { widget } },
    h('div', { class: 'tradingview-widget-container__widget' }),
    h(
      'div',
      { class: 'tradingview-widget-copyright hint' },
      h('a', { attrs: { href: 'https://kr.tradingview.com/', rel: 'noopener nofollow', target: '_blank' } }, 'TradingView'),
      ' 제공 차트',
    ),
  );
  box.append(script);
  container.append(box);
  return () => box.remove();
};
