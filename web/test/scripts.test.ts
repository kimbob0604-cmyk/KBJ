// 빌드 검사 스크립트 — 공개 번들 금지 문자열·크기 예산, 공개 데이터 합치기 규칙(§6.6·§7.3·§7.4)
import { describe, expect, it } from 'vitest';
import { BUDGET_GZIP, checkBudget, LOGIN_FORBIDDEN, PUBLIC_FORBIDDEN, scanText } from '../scripts/check-bundle.mjs';
import {
  DART_ONLY,
  dirDigest,
  loginSourcePaths,
  manifestDirs,
  nonDartSourcePaths,
  planMerge,
  sha256,
  SUBDIR_JSON_RE,
  SUBDIR_XLSX_RE,
  watchTracePaths,
} from '../scripts/merge-public-data.mjs';
import { PUBLIC_DOWNLOAD, PUBLIC_SUBDIR } from '../src/data/static';
import manifest from './fixtures/public/manifest.json';
import calendar from './fixtures/public/calendar.json';
import events from './fixtures/public/events.json';
// P4 픽스처는 파이썬 내보내기가 만든 그대로(바이트가 요약 해시에 들어가 ?raw 로 읽는다 —
// `uv run python -m tests.unit.public_export.pub_rows` 로 다시 만든다)
import manifestP4 from './fixtures/public/manifest_p4.json?raw';
import corpIndex from './fixtures/public/corp_index.json?raw';
import filingsFeed from './fixtures/public/filings_feed.json?raw';
import earningsPrelim from './fixtures/public/earnings_prelim.json?raw';
import overhangRecent from './fixtures/public/overhang_recent.json?raw';
import insiderRecent from './fixtures/public/insider_recent.json?raw';
import earningsSchedule from './fixtures/public/earnings_schedule.json?raw';
import fin1 from './fixtures/public/fin/Q00001.json?raw';
import fin2 from './fixtures/public/fin/Q00002.json?raw';
import fin3 from './fixtures/public/fin/Q00003.json?raw';
import fin5 from './fixtures/public/fin/Q00005.json?raw';
import report1 from './fixtures/public/reports/Q00001.json?raw';
import report1Xlsx from './fixtures/public/reports/Q00001.xlsx?raw';

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

describe('merge-public-data: P4 하위 폴더(fin·reports)·DART 전용 파일', () => {
  const p4: Record<string, string> = {
    'manifest.json': manifestP4,
    'calendar.json': JSON.stringify(calendar),
    'events.json': JSON.stringify(events),
    'corp_index.json': corpIndex,
    'filings_feed.json': filingsFeed,
    'earnings_prelim.json': earningsPrelim,
    'overhang_recent.json': overhangRecent,
    'insider_recent.json': insiderRecent,
    'earnings_schedule.json': earningsSchedule,
    'fin/Q00001.json': fin1,
    'fin/Q00002.json': fin2,
    'fin/Q00003.json': fin3,
    'fin/Q00005.json': fin5,
    'reports/Q00001.json': report1,
    'reports/Q00001.xlsx': report1Xlsx,
  };
  const man = JSON.parse(manifestP4) as { files: { name: string }[]; dirs: { name: string; count: number }[] };
  const plan = (files: Record<string, string | Uint8Array>, m: unknown = man) => planMerge(m, files);

  it('파이썬이 만든 산출물을 그대로 받아들인다(하위 폴더는 폴더 한 항목 — 개수·크기·요약 해시)', () => {
    const r = plan(p4);
    expect(r.errors).toEqual([]);
    expect(r.ignored).toEqual([]);
    expect(r.copy).toEqual([
      ...man.files.map((e) => e.name),
      'fin/Q00001.json',
      'fin/Q00002.json',
      'fin/Q00003.json',
      'fin/Q00005.json',
      'reports/Q00001.json',
      'reports/Q00001.xlsx',
      'manifest.json',
    ]);
    expect(manifestDirs(man).map((d) => [d.name, d.count])).toEqual([
      ['fin', 4],
      ['reports', 2],
    ]);
  });

  it('하위 폴더 파일을 바꾸거나 더하면 아무것도 복사하지 않는다', () => {
    const tampered = plan({ ...p4, 'fin/Q00001.json': fin1.replace('합성전자', '합성전자X') });
    expect(tampered.copy).toEqual([]);
    expect(tampered.errors.join()).toContain('fin/: manifest 요약 해시와 다름');
    expect(plan({ ...p4, 'fin/Q99999.json': fin3 }).errors.join()).toContain('fin/: manifest 개수와 다름');
    expect(plan({ ...p4, 'fin/q00001.json': fin1 }).errors.join()).toContain('fin/q00001.json: 허용하지 않는 이름');
  });

  it('하위 폴더·DART 전용 파일의 원천은 DART 하나 — 다른 원천·관심종목 흔적·prv_ 는 실패', () => {
    const env = JSON.parse(fin3) as Record<string, unknown>;
    const other = JSON.stringify({ ...env, data: { rows: [{ source: '한국은행' }] } });
    const watch = JSON.stringify({ ...env, data: { is_watch: true } });
    const cases: [string, string][] = [
      [other, 'source 가 DART 가 아님($.data.rows[0].source)'],
      [watch, '관심종목 흔적 키($.data.is_watch)'],
      [fin3.replace('합성홀딩스', 'prv_journal'), "로그인 등급 스키마 이름('prv_' 규칙)"],
    ];
    for (const [text, msg] of cases) {
      const r = plan({ ...p4, 'fin/Q00003.json': text });
      expect(r.copy, msg).toEqual([]);
      expect(r.errors.join(), msg).toContain(msg);
    }
    const feed = JSON.parse(filingsFeed) as Record<string, unknown>;
    const r = plan({ ...p4, 'filings_feed.json': JSON.stringify({ ...feed, source: 'KOSIS' }) });
    expect(r.errors.join()).toContain('filings_feed.json: source 가 DART 가 아님($.source)');
    expect(nonDartSourcePaths({ a: [{ source: 'DART' }, { source: 'X' }] })).toEqual(['$.a[1].source']);
    expect(watchTracePaths({ rows: [{ watchlist: [] }] })).toEqual(['$.rows[0].watchlist']);
    const names = ['corp_index', 'earnings_prelim', 'earnings_schedule', 'filings_feed', 'insider_recent', 'overhang_recent'];
    expect([...DART_ONLY].sort()).toEqual(names.map((n) => `${n}.json`));
  });

  it('엑셀은 짝 JSON 의 xlsx_sha256 과 같아야 한다', () => {
    const r = plan({ ...p4, 'reports/Q00001.xlsx': new Uint8Array([1, 2, 3]) });
    expect(r.copy).toEqual([]);
    expect(r.errors.join()).toContain('reports/Q00001.xlsx: 짝 JSON 의 xlsx_sha256 과 다름');
    const noXlsx = { ...p4 };
    delete noXlsx['reports/Q00001.xlsx'];
    expect(plan(noXlsx).errors.join()).toContain('짝 JSON 은 엑셀이 있다는데 파일이 없음');
  });

  it('manifest 에 폴더 항목이 없으면 하위 폴더 파일은 건너뛴다(P3 manifest)', () => {
    const files = {
      'manifest.json': JSON.stringify(manifest),
      'calendar.json': JSON.stringify(calendar),
      'events.json': JSON.stringify(events),
      'fin/Q00001.json': fin1,
    };
    const r = plan(files, manifest);
    expect(r.errors).toEqual([]);
    expect(r.ignored).toEqual(['fin/Q00001.json']);
    expect(r.copy).toEqual(['calendar.json', 'events.json', 'manifest.json']);
  });

  it('모르는·겹친 폴더 항목은 실패', () => {
    expect(plan(p4, { ...man, dirs: [...man.dirs, { name: 'secret', count: 0 }] }).errors.join()).toContain(
      'secret/: 허용하지 않는',
    );
    expect(plan(p4, { ...man, dirs: [...man.dirs, man.dirs[0]] }).errors.join()).toContain('fin/: 허용하지 않는(또는 겹친)');
  });

  it('요약 해시 정의(경로 순 `경로<탭>해시<줄바꿈>` 의 sha256)', () => {
    const a: [string, string][] = [
      ['fin/A00002.json', '2'.repeat(64)],
      ['fin/A00001.json', '1'.repeat(64)],
    ];
    expect(dirDigest(a)).toBe(sha256(`fin/A00001.json\t${'1'.repeat(64)}\nfin/A00002.json\t${'2'.repeat(64)}\n`));
  });

  it('하위 폴더 이름 규칙이 프런트(static.ts)와 같다', () => {
    for (const key of ['fin/Q00001', 'reports/005930', 'fin/q00001', 'fin/Q0001', 'other/Q00001', 'fin/../x']) {
      expect(SUBDIR_JSON_RE.test(`${key}.json`), key).toBe(PUBLIC_SUBDIR.test(key));
    }
    for (const key of ['reports/Q00001.xlsx', 'fin/Q00001.xlsx', 'reports/q00001.xlsx']) {
      expect(SUBDIR_XLSX_RE.test(key), key).toBe(PUBLIC_DOWNLOAD.test(key));
    }
  });
});
