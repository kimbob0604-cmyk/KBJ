// 빌드 검사 스크립트 — 공개 번들 금지 문자열·크기 예산, 공개 데이터 합치기 규칙(§6.6·§7.3·§7.4)
import { describe, expect, it } from 'vitest';
import { BUDGET_GZIP, checkBudget, LOGIN_FORBIDDEN, PUBLIC_FORBIDDEN, scanText } from '../scripts/check-bundle.mjs';
import { loginSourcePaths, planMerge } from '../scripts/merge-public-data.mjs';
import manifest from './fixtures/public/manifest.json';
import calendar from './fixtures/public/calendar.json';
import events from './fixtures/public/events.json';

describe('check-bundle: 공개 번들', () => {
  const bad = [
    'fetch("/api/market/summary")',
    'x="/telegram/webhook"',
    'post("auth/login")',
    'h["X-KBJ-CSRF"]=t',
    'h["x-kbj-csrf"]=t',
    'env.KBJ_WEB_USER',
    'document.cookie.includes("__Host-kbj_session")',
  ];
  it.each(bad)('찾는다: %s', (line) => {
    expect(scanText('a.js', line, PUBLIC_FORBIDDEN).length).toBeGreaterThan(0);
  });

  it('깨끗한 번들은 0, 결과에는 규칙·줄만(찾은 값 없음)', () => {
    expect(scanText('a.js', 'fetch("./data/calendar.json");const k="kbj-skin"', PUBLIC_FORBIDDEN)).toEqual([]);
    const f = scanText('a.js', 'ok\nfetch("/api/x")', PUBLIC_FORBIDDEN);
    expect(f).toEqual([{ file: 'a.js', line: 2, rule: 'api_path', why: '로그인 API 경로' }]);
  });
});

describe('check-bundle: 로그인 번들', () => {
  it('TradingView 주소를 찾고, 속성 이름(tradingview)만으로는 걸리지 않는다', () => {
    expect(scanText('b.js', 'src="https://s3.tradingview.com/external-embedding/embed-widget-x.js"', LOGIN_FORBIDDEN).length).toBe(2);
    expect(scanText('b.js', 'https://www.TradingView-Widget.com/', LOGIN_FORBIDDEN).length).toBe(1);
    expect(scanText('b.js', 'ctx.tradingview?{tradingview:o}:{}', LOGIN_FORBIDDEN)).toEqual([]);
  });
});

describe('check-bundle: 크기 예산(gzip)', () => {
  it('JS·CSS 합계, sw.js 는 제외', () => {
    const r = checkBudget(
      [
        { name: 'assets/a.js', bytes: 50_000 },
        { name: 'assets/b.js', bytes: 20_000 },
        { name: 'sw.js', bytes: 99_999 },
        { name: 'assets/a.css', bytes: 1_000 },
      ],
      BUDGET_GZIP.public,
    );
    expect(r.js).toBe(70_000);
    expect(r.findings.map((f) => f.rule)).toEqual(['budget_js']);
    expect(checkBudget([{ name: 'assets/a.js', bytes: 70_000 }], BUDGET_GZIP.login).findings).toEqual([]);
  });
});

describe('merge-public-data', () => {
  const files = {
    'manifest.json': JSON.stringify(manifest),
    'calendar.json': JSON.stringify(calendar),
    'events.json': JSON.stringify(events),
    'stray.json': '{}',
  };

  it('manifest 에 적힌 파일 + manifest 만 복사, 나머지는 건너뜀', () => {
    const p = planMerge(manifest, files);
    expect(p.errors).toEqual([]);
    expect(p.copy).toEqual(['calendar.json', 'events.json', 'manifest.json']);
    expect(p.ignored).toEqual(['stray.json']);
  });

  it('로그인 등급 출처 이름이 source 에 있으면 아무것도 복사하지 않는다', () => {
    const m = { files: [{ ...manifest.files[0], source: 'KRX 일별 시세' }, manifest.files[1]] };
    const p = planMerge(m, files);
    expect(p.copy).toEqual([]);
    expect(p.errors.join()).toContain('로그인 등급 출처');
    const env = { ...calendar, source: 'KIS(잠정)' };
    const p2 = planMerge(manifest, { ...files, 'calendar.json': JSON.stringify(env) });
    expect(p2.copy).toEqual([]);
    expect(p2.errors.join()).toContain('calendar.json: source 에 로그인 등급 출처 이름($.source)');
  });

  it('봉투 안 행·칸의 source 도 검사한다(깊이 무관, 값이 아니라 경로만 알린다)', () => {
    const nested = { ...calendar, data: { days: [{ d: '2026-10-07', cell: { source: 'krx+kis', v: 1 } }] } };
    const p = planMerge(manifest, { ...files, 'calendar.json': JSON.stringify(nested) });
    expect(p.copy).toEqual([]);
    expect(p.errors.join()).toContain('$.data.days[0].cell.source');
    expect(p.errors.join()).not.toContain('krx+kis');
    expect(loginSourcePaths({ rows: [{ source: 'kbj.core.calendar' }, { source: 'ETF_ISSUERS' }] })).toEqual([
      '$.rows[1].source',
    ]);
  });

  it('이름·원천·시각·품질 검사', () => {
    const entry = manifest.files[0]!;
    const cases: [Record<string, unknown>, string][] = [
      [{ ...entry, name: '../x.json' }, '허용하지 않는 이름'],
      [{ ...entry, name: '.env.json' }, '허용하지 않는 이름'],
      [{ ...entry, name: 'manifest.json' }, '허용하지 않는 이름'],
      [{ ...entry, source: '' }, 'source 없음'],
      [{ ...entry, as_of: '2026-10-07T05:30:00' }, 'as_of'],
      [{ ...entry, quality: 'good' }, 'quality'],
      [{ ...entry, name: 'missing.json' }, '파일이 없음'],
    ];
    for (const [e, msg] of cases) {
      const p = planMerge({ files: [e] }, files);
      expect(p.copy, msg).toEqual([]);
      expect(p.errors.join(), msg).toContain(msg);
    }
    expect(planMerge({ files: [entry] }, { 'calendar.json': 'not json' }).errors.join()).toContain('JSON 이 아님');
    expect(planMerge({ nope: 1 }, files).errors.join()).toContain('files');
  });

  it('manifest 객체 모양·이름에 .json 없어도', () => {
    const m = { files: { calendar: { source: 'S', as_of: '2026-10-07T05:30:00+09:00', quality: 'ok' } } };
    expect(planMerge(m, files).copy).toEqual(['calendar.json', 'manifest.json']);
  });
});
