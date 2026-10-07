"""
실시간 보드 — 고정 셸(web/app.py) + 데이터 JSON(web/payload.py).

여기서 잡으려는 것은 조용히 깨지는 세 가지다.
  1. 셸에 그날 데이터가 섞여 들어간다 → 링크가 옛 화면에 멈춘다
  2. 페이로드가 없는 값을 0 으로 채운다 → '계산 안 됨' 이 '0' 이 된다
  3. 보관 목록이 최신 생성 시각을 안 싣는다 → 화면이 갱신을 못 알아챈다
셋 다 화면을 한참 보고 있어야 알아차린다. 기계로 막는다.
"""
import json
import os
import shutil
import tempfile
import unittest

from ..web import app as A
from ..web import payload as P
from ..web import site as S


NEWHIGH = dict(
    as_of='2026-09-01', generated_at='2026-09-01T16:12:40+09:00',
    basis='close', labels=dict(hist='역사적', w52='52주', d60='60일'),
    displayed=['hist', 'w52', 'd60'],
    counts_high=dict(hist=2, w52=5, d60=9), counts_close=dict(hist=1, w52=4, d60=8),
    thresholds=dict(lookback=dict(d60=60, w52=252),
                    proximity=dict(max_gap_pct=5.0, min_mktcap_eok=1000,
                                   narrow_days=5)),
    min_turnover_eok=50, min_mktcap_eok=1000,
    achieved=[dict(code='005930', name='삼성전자', stage='메모리',
                   theme_name='반도체', label='w52', status='신규',
                   chg_pct=3.21, turnover=12345.0, mktcap=4000000.0,
                   vol_mult=2.4, kind='common',
                   high_basis=dict(label='hist'), close_basis=dict(label='w52')),
              # 고가로만 뚫고 종가로는 못 넘은 종목. 거래량 배수도 못 쟀다 —
              # 0 이 아니라 '없음' 이어야 한다.
              dict(code='000660', name='SK하이닉스', theme_name='반도체',
                   label=None, chg_pct=None, turnover=999.0,
                   high_basis=dict(label='w52'), close_basis=dict(label=None))],
    proximity=[dict(code='003550', name='LG', theme_name='지주',
                    near_kind='d60', near_gap=0.4, near_narrow5=-36.2,
                    turnover=371.0, mktcap=178860.0, vol_mult=1.0,
                    resistance=dict(d60=1.6, w52=9.9),
                    resistance_label=dict(d60='얇음', w52='두꺼움'))],
)
SECTORS = dict(themes=[dict(theme='semi', name='반도체', chg_pct=1.2,
                            breadth=dict(up=3, flat=1, down=2))],
               heatmap_theme=[dict(group='반도체', chg_pct=1.2, cells=[
                   dict(code='005930', name='삼성전자', turnover=12345.0,
                        mktcap=4000000.0, chg_pct=3.21, newhigh=True)])],
               heatmap_sector=[])
MARKET = dict(indices=dict(KOSPI=dict(label='코스피', close=3210.5, chg_pct=0.97),
                           KOSDAQ=dict(label='코스닥', close=None, chg_pct=None)),
              fx=dict(value=1384.8, chg_pct=-0.42),
              flows=dict(KOSPI=dict(unit='억원', by_date={
                  '2026-09-01': {'외국인': 2100, '기관계': -1800}})),
              missing=['투자자별 수급(개인): 소스 미확보'])
UNIVERSE = dict(n=2686)


def _payload(**kw):
    a = dict(newhigh=NEWHIGH, sectors=SECTORS, market=MARKET, events={},
             universe=UNIVERSE, meta={})
    a.update(kw)
    return P.build(a.pop('newhigh'), a.pop('sectors'), a.pop('market'),
                   a.pop('events'), a.pop('universe'), a.pop('meta'), **a)


class Shell(unittest.TestCase):
    """고정 셸 — 데이터가 없어야 하고, 매일 같은 파일이어야 한다."""

    def setUp(self):
        self.doc = A.build()

    def test_no_board_data_in_the_shell(self):
        # 종목명·수치가 한 글자라도 들어가면 그 순간부터 셸이 매일 바뀐다.
        for s in ('삼성전자', 'SK하이닉스', '2026-09-01', '005930'):
            self.assertNotIn(s, self.doc, f'{s} 가 셸에 박혔다')

    def test_title_has_no_date(self):
        # 갤러리·탭에서 사람이 찾는 이름이다. 날짜가 붙으면 매일 다른 페이지다.
        self.assertIn(f'<title>{A.TITLE}</title>', self.doc)

    def test_shell_is_a_whole_document(self):
        low = self.doc.lower()
        for tag in ('<!doctype', '<html', '</html>', '<body', '</body>'):
            self.assertIn(tag, low)

    def test_css_and_js_are_inlined(self):
        # 셸과 스크립트의 캐시가 어긋나면 옛 스크립트가 새 셸을 그린다.
        self.assertIn('<style>', self.doc)
        self.assertNotIn('app.css"', self.doc)
        self.assertNotIn('app.js"', self.doc)

    def test_it_fetches_the_data_file(self):
        self.assertIn('latest.json', self.doc)

    def test_build_is_deterministic(self):
        # 두 번 만들어 다르면 데이터를 안 바꿔도 커밋에 올라온다.
        self.assertEqual(self.doc, A.build())


class Payload(unittest.TestCase):

    def setUp(self):
        self.p = _payload()

    def test_basis_is_translated_for_the_screen(self):
        self.assertEqual(self.p['basis'], 'cl')
        self.assertEqual(_payload(newhigh=dict(NEWHIGH, basis='high'))['basis'], 'hi')

    def test_both_bases_are_carried_by_name(self):
        """화면이 토글로 두 기준을 바꿔 본다. 한쪽만 실으면 토글이 거짓말한다.

        `label` 은 기본 기준이라 설정에 따라 가리키는 기준이 바뀐다. 그걸
        '고가' 로 짐작해 실으면 종가 라벨이 고가 자리에 앉는다.
        """
        x = self.p['achieved'][0]
        self.assertEqual(x['high_label'], 'hist')
        self.assertEqual(x['close_label'], 'w52')
        self.assertNotIn('label', x, '기본 기준 라벨을 그대로 실으면 안 된다')

    def test_high_only_row_keeps_its_high_label(self):
        # 기본이 종가일 때 고가로만 뚫은 종목이 빠지면 '고가 기준' 토글이 빈 표가 된다.
        x = self.p['achieved'][1]
        self.assertEqual(x['high_label'], 'w52')
        self.assertIsNone(x['close_label'])

    def test_counts_are_split_by_basis(self):
        self.assertEqual(self.p['counts_high'], dict(hist=2, w52=5, d60=9))
        self.assertEqual(self.p['counts_close'], dict(hist=1, w52=4, d60=8))

    def test_uncomputed_values_are_absent_not_zero(self):
        x = self.p['achieved'][1]
        self.assertNotIn('chg_pct', x, '계산 안 된 값이 0 으로 들어갔다')
        self.assertNotIn('vol_mult', x)

    def test_resistance_is_the_one_for_that_row(self):
        # 저항두께는 기준별로 따로 잰다. 그 종목이 쓰는 기준 것만 실어야 한다.
        x = self.p['proximity'][0]
        self.assertEqual(x['resistance'], 1.6)
        self.assertEqual(x['resistance_label'], '얇음')

    def test_index_without_close_is_kept_with_a_null(self):
        # 값을 못 받은 지수를 빼 버리면 화면이 '그런 지수는 없다' 로 읽는다.
        kd = [x for x in self.p['market']['indices'] if x['sym'] == 'KOSDAQ'][0]
        self.assertIsNone(kd['close'])

    def test_flows_keep_source_order(self):
        who = [f['who'] for f in self.p['market']['flows']]
        self.assertEqual(who, ['기관계', '외국인'])

    def test_notices_match_the_rendered_board(self):
        """배너 문구는 구운 화면과 실시간 화면이 같아야 한다."""
        from ..web import render as R
        miss, scope = R.notices(NEWHIGH, MARKET, {}, {}, UNIVERSE)
        self.assertEqual(self.p['notices']['miss'], miss)
        self.assertEqual(self.p['notices']['scope'], scope)

    def test_heatmap_is_carried(self):
        self.assertEqual(len(self.p['heatmap']['theme']), 1)

    def test_dump_keeps_korean_readable(self):
        self.assertIn('삼성전자', P.dump(self.p))

    def test_dump_is_valid_json(self):
        self.assertEqual(json.loads(P.dump(self.p))['as_of'], '2026-09-01')


class WriteData(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='livetest-')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, asof, **kw):
        p = _payload()
        p['as_of'] = asof
        p['generated_at'] = f'{asof}T16:12:40+09:00'
        return S.write_data(self.root, asof, p, log=lambda *a: None, **kw)

    def _idx(self):
        with open(os.path.join(self.root, 'api', 'index.json'), encoding='utf-8') as f:
            return json.load(f)

    def test_latest_and_archive_are_written(self):
        self._write('2026-09-01')
        self.assertTrue(os.path.exists(os.path.join(self.root, 'api', 'latest.json')))
        self.assertTrue(os.path.exists(os.path.join(self.root, 'api', 'd', '2026-09-01.json')))

    def test_latest_is_replaced_not_appended(self):
        self._write('2026-09-01')
        self._write('2026-09-02')
        with open(os.path.join(self.root, 'api', 'latest.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['as_of'], '2026-09-02')

    def test_index_carries_generated_at(self):
        """화면은 이 한 값만 다시 받아 갱신 여부를 판단한다. 없으면 매번 본문을 받는다."""
        self._write('2026-09-01')
        self.assertEqual(self._idx()['generated_at'], '2026-09-01T16:12:40+09:00')

    def test_index_lists_newest_first(self):
        for d in ('2026-09-01', '2026-09-03', '2026-09-02'):
            self._write(d)
        j = self._idx()
        self.assertEqual(j['dates'], ['2026-09-03', '2026-09-02', '2026-09-01'])
        self.assertEqual(j['latest'], '2026-09-03')

    def test_old_archives_are_pruned(self):
        for i in range(1, 7):
            self._write(f'2026-09-0{i}', keep_days=3)
        left = sorted(os.listdir(os.path.join(self.root, 'api', 'd')))
        self.assertEqual(left, ['2026-09-04.json', '2026-09-05.json', '2026-09-06.json'])

    def test_publishing_twice_same_day_is_idempotent(self):
        self._write('2026-09-01')
        self._write('2026-09-01')
        self.assertEqual(self._idx()['dates'], ['2026-09-01'])


class PublishSplit(unittest.TestCase):
    """루트는 셸, 보관본·아티팩트는 구운 문서 — 두 문서가 섞이면 안 된다."""

    BAKED = ('<!doctype html><html><head><title>보드 2026-09-01</title></head>'
             '<body>코스맥스 +7.07%</body></html>')

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='livetest-')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _read(self, *parts):
        with open(os.path.join(self.root, *parts), encoding='utf-8') as f:
            return f.read()

    def test_index_gets_the_shell_and_archive_gets_the_bake(self):
        S.publish(self.root, '2026-09-01', self.BAKED, log=lambda *a: None,
                  index_html='<!doctype html><html><head><title>셸</title>'
                             '</head><body></body></html>')
        self.assertIn('셸', self._read('index.html'))
        self.assertNotIn('코스맥스', self._read('index.html'))
        self.assertIn('코스맥스', self._read('d', '2026-09-01.html'))

    def test_artifact_comes_from_the_bake_not_the_shell(self):
        """아티팩트에는 받아올 사이트가 없다. 데이터가 박힌 쪽이어야 한다."""
        S.publish(self.root, '2026-09-01', self.BAKED, log=lambda *a: None,
                  index_html='<!doctype html><html><head><title>셸</title>'
                             '</head><body></body></html>')
        self.assertIn('코스맥스', self._read('artifact.html'))

    def test_without_index_html_both_get_the_same_document(self):
        # 옛 호출 방식이 그대로 돌아야 한다.
        S.publish(self.root, '2026-09-01', self.BAKED, log=lambda *a: None)
        self.assertEqual(self._read('index.html'), self.BAKED)


if __name__ == '__main__':
    unittest.main()
