"""
조립 시험 — 합성 일봉으로 state 를 만들고 화면까지 나오는지 본다.

외부 호출이 막힌 환경에서도 배선 전체(수집 캐시 모양 → engine → state →
template 치환)를 잰다. 숫자의 옳고 그름은 test_engine.py 가 골든으로 보고,
여기서는 **모양과 연결**만 본다.

합성값은 절대 화면으로 새면 안 된다. board 의 --demo 격리(D-047)와 같은 이유로
출력 경로를 임시 폴더로 돌려놓고 돌린다.
"""
import json
import os
import random
import shutil
import tempfile
import unittest

from .. import build as B
from .. import engine as E


def synth_daily(codes, days=400, seed=7):
    """등락률만 있는 합성 일봉. 액면분할·거래정지도 섞는다."""
    rnd = random.Random(seed)
    # 연도를 넘겨야 한다. YTD 는 작년 마지막 거래일을 기준으로 잡으므로
    # 한 해 안에서만 만든 표본으로는 그 경로를 아예 밟지 못한다.
    allday = [f'{y}{m:02d}{d:02d}'
              for y in (2025, 2026) for m in range(1, 13) for d in range(1, 29)]
    dates = allday[-days:]
    assert dates[0][:4] == '2025', '표본이 연도를 넘지 않는다 — days 를 늘려라'
    out = {}
    for i, day in enumerate(dates):
        rows = []
        for j, c in enumerate(codes):
            # 20종목마다 하나는 40일을 통째로 거래정지시킨다. 룩백 안에 들도록
            # 끝에서 80~40일 구간에 둔다 — 더 과거에 두면 1Y 창 밖으로 빠진다.
            if j % 20 == 3 and len(dates) - 80 <= i < len(dates) - 40:
                continue
            rows.append(dict(
                c=c, n=f'종목{j:04d}', m='KOSPI' if j % 3 else 'KOSDAQ',
                p=1000 + rnd.randint(-50, 50),
                f=round(rnd.uniform(-5, 5), 2),
                v=rnd.randint(1000, 10 ** 6),
                t=float(rnd.randint(2, 50)) * 10 ** 9,
                k=float(rnd.randint(1, 900)) * 10 ** 9,
                s=10 ** 7))
        out[day] = rows
    return out


class 조립(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        themes = B.load_themes()
        codes = []
        for g in themes['groups']:
            for sec in g['sectors']:
                codes.extend(sec.get('members') or [])
        cls.codes = sorted(set(codes))[:400]
        cls.daily = synth_daily(cls.codes)
        cls.state = B.build_state(cls.daily, failed=[])

    def test_state의_최상위_모양이_화면_계약과_같다(self):
        for k in ('meta', 'periods', 'indices', 'indexDD', 'groups',
                  'stocks', 'charts', 'leaders', 'fin', 'etf'):
            self.assertIn(k, self.state, k)

    def test_미착수_묶음은_빈dict가_아니라_None이다(self):
        """자바스크립트에서 {} 는 참이다.

        화면은 `S.fin ? ... : '미적용'` 으로 가른다. 빈 dict 를 주면 가드를
        통과한 뒤 S.fin.years.join(...) 에서 죽는다. 눈에 안 띄는 방식으로
        페이지 전체가 하얘지므로 계약으로 박아 둔다.
        """
        for k in ('fin', 'etf'):
            self.assertIsNone(self.state[k], f'{k} 는 None 이어야 한다')

    def test_지수는_값이_없어도_껍데기를_채운다(self):
        """화면이 S.indices['코스피'].r['1M'] 을 가드 없이 읽는다.

        지수 수집이 실패해도 이 자리가 비면 페이지가 통째로 죽는다. 값은
        None 이어도 모양은 있어야 한다.
        """
        ix = self.state['indices']
        for nm in ('코스피', '코스닥'):
            self.assertIn(nm, ix)
            self.assertIn('r', ix[nm])
            self.assertIn('1M', ix[nm]['r'])
        self.assertIsInstance(self.state['indexDD'], dict)

    def test_지수를_받으면_수익률이_채워진다(self):
        rows = {'코스피': [dict(d=d, c=100.0 + i) for i, d in enumerate(sorted(self.daily))]}
        st = B.build_state(self.daily, failed=[], index_rows=rows)
        self.assertIsNotNone(st['indices']['코스피']['close'])
        self.assertIsNotNone(st['indices']['코스피']['r']['1M'])
        # 못 받은 코스닥은 껍데기만 남는다 — 0 으로 채우지 않는다.
        self.assertIsNone(st['indices']['코스닥']['close'])
        self.assertIsNone(st['indices']['코스닥']['r']['1M'])

    def test_유니버스와_제외사유의_합이_상장수와_맞다(self):
        m = self.state['meta']
        self.assertEqual(m['universeCount'] + sum(m['excluded'].values()),
                         m['totalListed'])

    def test_기간이_8개고_기준일이_붙는다(self):
        keys = [p['key'] for p in self.state['periods']]
        self.assertEqual(set(keys), {'1D', '1W', '2W', '1M', '3M', '6M', '1Y', 'YTD'})
        for p in self.state['periods']:
            self.assertIsNotNone(p['baseDate'], p['key'])

    def test_작년_거래일이_없으면_YTD를_지어내지_않는다(self):
        """기준이 없으면 0% 가 아니라 None 이다."""
        only2026 = {d: r for d, r in self.daily.items() if d.startswith('2026')}
        st = B.build_state(only2026, failed=[])
        ytd = [p for p in st['periods'] if p['key'] == 'YTD'][0]
        self.assertIsNone(ytd['baseDate'])
        vals = [s['r']['YTD'] for s in st['stocks'].values()]
        self.assertTrue(all(v is None for v in vals), '기준 없이 YTD 가 나왔다')

    def test_상위테마는_하위를_합친_것이다(self):
        for g in self.state['groups']:
            if g['key'] != '테마':
                continue
            by = {s['name']: s for s in g['sectors']}
            for s in g['sectors']:
                if s['level'] != 0 or not s['children']:
                    continue
                kids = {c for c in s['children'] if c in by}
                if not kids:
                    continue
                # 상위 종목수는 하위 합 이하다(복수배정이 있으면 중복을 뺀다).
                self.assertLessEqual(s['count'], sum(by[c]['count'] for c in kids),
                                     s['name'])

    def test_거래정지_구간이_보합으로_둔갑하지_않는다(self):
        """40일 멈춘 종목은 그 구간을 걸치는 기간 수익률이 N/A 여야 한다.

        결측 40일은 GAP_LIMIT(20)을 넘으므로 3M·6M·1Y 는 값을 내면 안 된다.
        반대로 멈춘 구간을 걸치지 않는 1D 는 정상으로 나와야 한다 — 한 종목의
        문제가 모든 기간을 통째로 지우면 그것도 틀린 것이다.
        """
        halted = [c for j, c in enumerate(self.codes) if j % 20 == 3]
        self.assertTrue(halted)
        checked = 0
        for c in halted:
            s = self.state['stocks'].get(c)
            if not s:
                continue
            checked += 1
            for p in ('3M', '6M', '1Y'):
                self.assertIsNone(s['r'].get(p), f'{c} {p} 가 멈춘 구간을 걸쳤는데 값이 있다')
            self.assertIsNotNone(s['r'].get('1D'), f'{c} 1D 까지 지워졌다')
        self.assertGreater(checked, 0, '멈춘 종목이 유니버스에 하나도 안 남았다')

    def test_섹터_지수낙폭이_채워지고_차트와_같은_계열을_쓴다(self):
        """ddIndex 와 charts 를 따로 만들면 화면의 선과 낙폭 숫자가 어긋난다.

        같은 계열이면 차트 마지막 값이 고점 대비 curDD 만큼 아래여야 한다.
        """
        ch = self.state['charts']
        filled = 0
        for g in self.state['groups']:
            if g['key'] not in ('테마', '밸류체인'):
                continue
            for sec in g['sectors']:
                line = ch['series'].get(f"{g['key']}/{sec['name']}")
                if not line or sec['ddIndex'] is None:
                    continue
                vals = [v for v in line if v is not None]
                if len(vals) < 2:
                    continue
                want = (vals[-1] / max(vals) - 1) * 100.0
                # 차트는 전송량을 줄이려고 소수 4자리로 반올림하고 ddIndex 는
                # 원값을 쓴다. 그래서 1e-5 언저리 차이는 정상이다. 계열 자체가
                # 다르면 몇 %p 단위로 벌어지므로 이 허용오차로도 충분히 걸린다.
                self.assertAlmostEqual(sec['ddIndex']['curDD'], want, places=2,
                                       msg=sec['name'])
                filled += 1
        self.assertGreater(filled, 10, 'ddIndex 가 채워진 섹터가 너무 적다')

    def test_구성종목이_없는_섹터는_ddIndex도_None(self):
        empty = [s for g in self.state['groups'] for s in g['sectors']
                 if s['count'] == 0]
        for s in empty:
            self.assertIsNone(s['ddIndex'], s['name'])

    def test_업종_묶음이_붙고_배정_못한_종목은_빠진다(self):
        from .. import industries as IND
        ind = IND.assign(self.codes, IND.load_knowledge())
        st = B.build_state(self.daily, failed=[], industry=ind)
        g = [x for x in st['groups'] if x['key'] == '업종']
        self.assertTrue(g, '업종 묶음이 안 붙었다')
        members = {c for s in g[0]['sectors'] for c in s['members']}
        # 배정된 종목만 들어간다 — 모르는 종목을 '기타' 로 몰지 않는다.
        for c in members:
            self.assertIn(c, ind, c)
        self.assertTrue(st['meta']['industryAvailable'])
        self.assertGreater(st['meta']['industryCoverage'], 0)

    def test_업종을_안_넘기면_묶음이_아예_없다(self):
        keys = [g['key'] for g in self.state['groups']]
        self.assertNotIn('업종', keys)
        self.assertFalse(self.state['meta']['industryAvailable'])

    def test_잠정치를_얹으면_확정일과_최신일이_갈린다(self):
        """얹은 날을 조용히 확정치인 척하지 않는다."""
        days = sorted(self.daily)
        pv = dict(date='20270101', asOf='20:10', source='네이버 금융',
                  traded=3, count=4, confirmedThrough=days[-1])
        daily = dict(self.daily)
        daily['20270101'] = [dict(r) for r in self.daily[days[-1]]]
        st = B.build_state(daily, failed=[], provisional=pv)
        m = st['meta']
        self.assertEqual(m['latestTradingDay'], '20270101')
        self.assertEqual(m['confirmedThrough'], days[-1])
        self.assertEqual(m['provisional']['source'], '네이버 금융')

    def test_잠정치가_없으면_확정일이_최신일이다(self):
        m = self.state['meta']
        self.assertIsNone(m['provisional'])
        self.assertEqual(m['confirmedThrough'], m['latestTradingDay'])

    def test_차트_계열이_날짜수와_길이가_같다(self):
        ch = self.state['charts']
        self.assertTrue(ch['series'])
        for name, line in ch['series'].items():
            self.assertEqual(len(line), len(ch['days']), name)

    def test_리더보드는_상하위가_뒤집힌_관계다(self):
        for p, v in self.state['leaders'].items():
            if len(v['top']) < 2:
                continue
            self.assertGreaterEqual(v['top'][0]['r'], v['top'][-1]['r'], p)
            self.assertLessEqual(v['bottom'][0]['r'], v['bottom'][-1]['r'], p)

    def test_화면이_만들어지고_자리표시자가_남지_않는다(self):
        tmp = tempfile.mkdtemp()
        old = B.OUT
        try:
            B.OUT = tmp
            n = B.render(self.state)
            self.assertGreater(n, 300 * 1024, '화면이 너무 작다 — 템플릿이 비었나')
            with open(os.path.join(tmp, 'index.html'), encoding='utf-8') as f:
                html = f.read()
            self.assertNotIn(B.TOKEN, html)
            self.assertIn('window.__STATE__', html)
            # 합성 데이터가 진짜처럼 보이지 않게, 이 시험은 docs/ 를 만지지 않는다.
            self.assertFalse(os.path.exists(os.path.join(tmp, '..', 'docs', 'kr', 'index.html')))
        finally:
            B.OUT = old
            shutil.rmtree(tmp, ignore_errors=True)

    def test_키가_산출물에_섞이면_중단한다(self):
        tmp = tempfile.mkdtemp()
        old, key = B.OUT, os.environ.get('DATAGO_KEY')
        try:
            B.OUT = tmp
            # state 안에 실제로 들어 있는 문자열을 키인 척 넣는다.
            os.environ['DATAGO_KEY'] = self.state['meta']['latestTradingDay'] + 'XXXX'
            self.state['meta']['leak'] = os.environ['DATAGO_KEY']
            with self.assertRaises(SystemExit):
                B.render(self.state)
        finally:
            self.state['meta'].pop('leak', None)
            B.OUT = old
            if key is None:
                os.environ.pop('DATAGO_KEY', None)
            else:
                os.environ['DATAGO_KEY'] = key
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
