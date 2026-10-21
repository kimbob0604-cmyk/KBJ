// P6 공통 부품 — CSV 내보내기(ui/csv.ts, D-P6-19)·페이지 7 탭 틀(p7_trade.ts — 웨이브 1 FND).
import { describe, expect, it, vi } from 'vitest';
import { TAB_MODULES, TRADE_TABS } from '../src/pages/p7_trade';
import { BOM, csvName, downloadCsv, toCsv } from '../src/ui/csv';

interface Row {
  hs: string;
  name: string;
  usd: number | null;
  yoy?: number;
}

const cols = [
  { label: 'HS', value: 'hs' as const },
  { label: '품목명', value: 'name' as const },
  { label: '금액(달러)', value: 'usd' as const },
  { label: 'YoY', value: (r: Row) => r.yoy },
];

describe('ui/csv — BOM·이스케이프·빈 값', () => {
  it('BOM 으로 시작하고 CRLF 로 줄을 나눈다', () => {
    const text = toCsv<Row>([{ hs: '8542', name: '집적회로', usd: 1200, yoy: 0.5 }], cols);
    expect(text.startsWith(BOM)).toBe(true);
    expect(text.slice(1)).toBe('HS,품목명,금액(달러),YoY\r\n8542,집적회로,1200,0.5\r\n');
  });

  it('쉼표·큰따옴표·줄바꿈은 큰따옴표로 감싸고 안의 따옴표는 두 번', () => {
    const text = toCsv<Row>([{ hs: '85', name: '전기기기, "부분품"\n기타', usd: null }], cols);
    expect(text).toContain('85,"전기기기, ""부분품""\n기타",,\r\n');
  });

  it('값이 없으면 빈 칸(0 아님) — null·undefined·NaN·무한', () => {
    const text = toCsv<Row>([{ hs: '30', name: 'x', usd: Number.NaN, yoy: Number.POSITIVE_INFINITY }], cols);
    expect(text.trimEnd().split('\r\n')[1]).toBe('30,x,,');
  });

  it('수식 주입 막기 — 문자열만 앞에 작은따옴표, 음수는 그대로', () => {
    const text = toCsv<Row>([{ hs: '=1+1', name: '@cmd', usd: -5 }], cols);
    expect(text.trimEnd().split('\r\n')[1]).toBe("'=1+1,'@cmd,-5,");
  });

  it('메타 줄(출처·기준)을 표 아래에', () => {
    const text = toCsv<Row>([], cols, { meta: [['출처', '관세청(공공데이터포털)'], ['기준', '2026-09']] });
    expect(text.slice(1).split('\r\n')).toEqual(['HS,품목명,금액(달러),YoY', '', '출처,관세청(공공데이터포털)', '기준,2026-09', '']);
  });

  it('파일 이름은 안전한 글자만, .csv 로 끝난다', () => {
    expect(csvName('trade items 2026-09')).toBe('trade_items_2026-09.csv');
    expect(csvName('../x.csv')).toBe('x.csv');
    expect(csvName('수출')).toBe('data.csv');
  });

  it('내려받기: Blob 주소로 a 를 눌렀다가 지우고 주소를 돌려준다(요청 없음)', () => {
    const created: Blob[] = [];
    const revoke = vi.fn();
    const clicks: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(`${this.download}|${this.href}`);
    });
    const name = downloadCsv('trade/flash', toCsv<Row>([], cols), {
      createUrl: (b) => {
        created.push(b);
        return 'blob:test-1';
      },
      revokeUrl: revoke,
    });
    expect(name).toBe('trade_flash.csv');
    expect(created[0]?.type).toBe('text/csv;charset=utf-8');
    expect(clicks).toEqual(['trade_flash.csv|blob:test-1']);
    expect(revoke).toHaveBeenCalledWith('blob:test-1');
    expect(document.querySelectorAll('a[download]')).toHaveLength(0);
    click.mockRestore();
  });
});

describe('페이지 7 탭 틀 — 웨이브 2 는 이 파일을 고치지 않는다', () => {
  it('탭과 모듈 표가 맞고, 맨 앞 종목 탭에 매핑 종목 랭킹·흐름(P10)', () => {
    expect(TRADE_TABS.map((t) => t.id)).toEqual(['stocks', 'industry', 'monthly', 'flash', 'items', 'countries', 'regions', 'surge', 'items104']);
    for (const t of TRADE_TABS) expect(TAB_MODULES[t.id].length, t.id).toBeGreaterThan(0);
    expect(TAB_MODULES.items).toHaveLength(1);
    expect(TAB_MODULES.stocks).toHaveLength(1);
  });
});
