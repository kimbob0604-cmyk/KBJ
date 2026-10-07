"""
화면 연기 시험 — 진짜 브라우저로 열어 본다.

state 의 모양만 맞추는 시험으로는 못 잡는 것이 있다. 실제로 이 시험을 붙이고
나서야 잡힌 것들:

  1. fin·etf 를 빈 dict 로 뒀더니 `S.fin ? ... : '미적용'` 가드를 통과했다.
     자바스크립트에서 {} 는 참이다. 그 뒤 S.fin.years.join() 에서 죽었다.
  2. S.indices['코스피'].r 을 화면이 가드 없이 읽는데 지수를 안 채웠다.
  3. 섹터에 momentum 이 없어 '불러오기 실패' 가 화면에 찍혔다.
  4. 구성종목이 없는 섹터의 stats 를 빈 dict 로 뒀더니 breadth.toFixed() 에서
     죽었다. None 이어야 화면이 '계산 가능한 종목이 없습니다' 로 간다.

넷 다 콘솔 오류 없이 **조용히** 화면 일부나 전체를 날리는 종류였다. 그래서
페이지 오류 0 을 계약으로 박는다.

playwright 나 크로미움이 없으면 건너뛴다 — 계산 시험은 그것 없이도 돌아야 한다.
"""
import glob
import os
import pathlib
import shutil
import tempfile
import unittest

from .. import build as B
from .test_build import synth_daily

try:
    from playwright.sync_api import sync_playwright
    HAVE_PW = True
except ImportError:
    HAVE_PW = False


def chromium_path():
    """환경에 설치된 크로미움. 없으면 None."""
    for pat in ('/opt/pw-browsers/chromium-*/chrome-linux/chrome',
                '/opt/pw-browsers/chromium/chrome-linux/chrome'):
        hit = sorted(glob.glob(pat))
        if hit:
            return hit[-1]
    return None


@unittest.skipUnless(HAVE_PW and chromium_path(), 'playwright/크로미움 없음')
class 화면(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        themes = B.load_themes()
        codes = sorted({c for g in themes['groups'] for s in g['sectors']
                        for c in (s.get('members') or [])})[:400]
        daily = synth_daily(codes)
        days = sorted(daily)
        rows = {'코스피': [dict(d=d, c=2500.0 + i) for i, d in enumerate(days)],
                '코스닥': [dict(d=d, c=800.0 + i * 0.3) for i, d in enumerate(days)]}
        # 재무도 합성으로 붙인다. fin 이 None 이면 카드 경로를 아예 안 밟는다.
        from .. import financials as FIN
        by = {c: {'fs_div': 'CFS',
                  'annual': [dict(year=y, rev=100.0 + y, op=10.0, ni=8.0)
                             for y in (2023, 2024, 2025)],
                  'quarterly': [dict(year=yy, q=q, label=f'{q}{str(yy)[2:]}',
                                     rev=50.0, op=5.0, ni=4.0)
                                for yy, q in ((2025, '2Q'), (2026, '1Q'), (2026, '2Q'))]}
              for c in codes[:120]}
        # 수급도 합성으로. flow 가 비면 수급 패널 경로를 아예 안 밟는다.
        import random as _r
        rnd = _r.Random(11)
        flow = {c: {d: dict(f=rnd.uniform(-50, 50), o=rnd.uniform(-50, 50),
                            p=rnd.uniform(-50, 50)) for d in days[-25:]}
                for c in codes[:200]}
        # ETF 도 합성으로. etf 가 None 이면 탭 경로를 아예 안 밟는다.
        from .. import etf as ETF
        names = [('069500', 'KODEX 200', 1), ('102110', 'TIGER 200', 1),
                 ('091160', 'KODEX 반도체', 2), ('396500', 'TIGER 반도체TOP10', 2),
                 ('122630', 'KODEX 레버리지', 3), ('114800', 'KODEX 인버스', 3),
                 ('360750', 'TIGER 미국S&P500', 4), ('114260', 'KODEX 국고채3년', 6)]
        listing = {c: dict(name=nm, tab=tb, price=10000 + i * 10, chg=0.5,
                           nav=10000.0 + i * 9, mktcap=5000.0, units=1e6,
                           volume=1e5, turnover=500.0)
                   for i, (c, nm, tb) in enumerate(names)}
        ehist = {c: {d: 10000.0 + i + j for j, d in enumerate(days)}
                 for i, (c, _, _) in enumerate(names)}
        # 업종과 당일 잠정치까지 붙여 모든 경로를 밟게 한다.
        from .. import industries as IND
        ind = IND.assign(codes, IND.load_knowledge())
        pv = dict(date=days[-1], asOf='20:10', source='네이버 금융',
                  traded=len(codes), count=len(codes),
                  confirmedThrough=days[-2])
        state = B.build_state(daily, failed=[], index_rows=rows,
                              fin=FIN.build(by), flow_by_code=flow,
                              etf=ETF.build(listing, ehist, days),
                              industry=ind, provisional=pv)
        cls.state = state

        cls.tmp = tempfile.mkdtemp()
        old = B.OUT
        try:
            B.OUT = cls.tmp
            B.render(state)
        finally:
            B.OUT = old
        cls.url = pathlib.Path(cls.tmp, 'index.html').resolve().as_uri()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, 'tmp', ''), ignore_errors=True)

    def test_열고_모든_패널을_눌러도_오류가_없다(self):
        errs = []
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=chromium_path(), args=['--no-sandbox'])
            pg = b.new_page(viewport={'width': 1440, 'height': 1200})
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console',
                  lambda m: errs.append('CONSOLE: ' + m.text) if m.type == 'error' else None)
            pg.goto(self.url, wait_until='load', timeout=60000)
            pg.wait_for_timeout(2500)

            # 테마 묶음에서 히트맵과 로테이션이 실제로 채워지는지.
            pg.get_by_text('테마 · Themes', exact=False).first.click()
            pg.wait_for_timeout(1200)
            rows = pg.evaluate("() => document.querySelectorAll('#heatmap tbody tr').length")
            points = pg.evaluate("""() => {
              const c = document.querySelector('#rotChart');
              const ch = (c && window.Chart && Chart.getChart) ? Chart.getChart(c) : null;
              return ch ? ch.data.datasets.reduce((a, d) => a + (d.data ? d.data.length : 0), 0) : 0;
            }""")

            # 눈에 보이는 단추를 두루 눌러 본다. 첫 화면만 멀쩡한 경우를 거른다.
            tabs = pg.locator('[data-tab], nav button, .tabs button, .seg button')
            for i in range(min(tabs.count(), 30)):
                try:
                    tabs.nth(i).click(timeout=2000)
                    pg.wait_for_timeout(300)
                except Exception:  # noqa: BLE001 — 안 눌리는 단추는 이 시험 대상이 아니다
                    pass
            # 재무 카드는 종목명 위에 data-tip 으로 붙는다. 실제로 생성됐는지 본다.
            tips = pg.evaluate(
                "() => document.querySelectorAll('[data-tip*=\"시가총액\"]').length")
            # 수급 표가 실제로 행을 만들었는지.
            flow_rows = pg.evaluate(
                "() => document.querySelectorAll('#flowTable tbody tr, #flow tbody tr').length")
            etf_rows = pg.evaluate(
                "() => document.querySelectorAll('#etfTable tbody tr').length")
            # 업종 묶음 단추와 '당일 잠정' 배지가 실제로 떴는지.
            has_industry = pg.evaluate(
                "() => !!document.body.innerText.match(/업종 · Industry/)")
            has_prov = pg.evaluate(
                "() => !!document.querySelector('#asofLag .badge.prov')")
            body = pg.inner_text('body')
            b.close()

        self.assertGreater(tips, 0, '재무·시총 카드가 하나도 안 붙었다')
        self.assertGreater(flow_rows, 0, '수급 표가 비었다')
        self.assertGreater(etf_rows, 0, 'ETF 표가 비었다')
        self.assertTrue(has_industry, '업종 묶음이 화면에 없다')
        self.assertTrue(has_prov, '당일 잠정 배지가 안 떴다')

        self.assertEqual(errs, [], f'페이지 오류: {errs[:3]}')
        self.assertGreater(rows, 10, '테마 히트맵이 비었다')
        self.assertGreater(points, 10, '로테이션 산점도가 비었다')
        for bad in ('불러오기 실패', 'undefined', 'NaN'):
            self.assertNotIn(bad, body, f'화면에 {bad} 가 찍혔다')


if __name__ == '__main__':
    unittest.main()
