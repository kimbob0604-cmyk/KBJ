// 서비스 워커 요청 분류 — 앱 셸·공개 데이터만, 그 밖(로그인 API 포함)은 가로채지 않는다(§6.4)
import { describe, expect, it } from 'vitest';
import { classify } from '../src/pwa/routes';

const scope = new URL('https://kbj.example/');
const pages = new URL('https://user.github.io/kbj/');
const c = (u: string, mode = 'cors', method = 'GET', sc = scope) => classify(new URL(u, sc), sc, method, mode);

describe('classify', () => {
  it('페이지 이동은 nav(index 만)', () => {
    expect(c('https://kbj.example/', 'navigate')).toBe('nav');
    expect(c('https://kbj.example/index.html', 'navigate')).toBe('nav');
    expect(c('https://kbj.example/other', 'navigate')).toBe('bypass');
  });

  it('앱 셸 파일은 asset', () => {
    expect(c('https://kbj.example/assets/main-AbC123.js')).toBe('asset');
    expect(c('https://kbj.example/assets/main-AbC123.css')).toBe('asset');
    expect(c('https://kbj.example/icon.svg')).toBe('asset');
    expect(c('https://kbj.example/manifest.webmanifest')).toBe('asset');
  });

  it('공개 데이터는 data', () => {
    expect(c('https://kbj.example/data/calendar.json')).toBe('data');
    expect(c('https://kbj.example/data/../x.json')).toBe('bypass');
    expect(c('https://kbj.example/data/sub/x.json')).toBe('bypass');
  });

  it('로그인 API·웹훅·쿼리·다른 출처·GET 아닌 요청은 가로채지 않는다', () => {
    expect(c('https://kbj.example/api/market/summary')).toBe('bypass');
    expect(c('https://kbj.example/api/auth/me')).toBe('bypass');
    expect(c('https://kbj.example/telegram/webhook', 'cors', 'POST')).toBe('bypass');
    expect(c('https://kbj.example/assets/main.js?v=1')).toBe('bypass');
    expect(c('https://s3.tradingview.com/external-embedding/x.js', 'no-cors')).toBe('bypass');
    expect(c('https://kbj.example/data/calendar.json', 'cors', 'POST')).toBe('bypass');
  });

  it('Pages 하위 경로(/kbj/)에서도 같은 규칙', () => {
    expect(c('https://user.github.io/kbj/', 'navigate', 'GET', pages)).toBe('nav');
    expect(c('https://user.github.io/kbj/assets/a-1.js', 'cors', 'GET', pages)).toBe('asset');
    expect(c('https://user.github.io/kbj/data/events.json', 'cors', 'GET', pages)).toBe('data');
    expect(c('https://user.github.io/other/assets/a-1.js', 'cors', 'GET', pages)).toBe('bypass');
  });
});
