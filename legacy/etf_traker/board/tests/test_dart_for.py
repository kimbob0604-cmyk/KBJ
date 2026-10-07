"""DART 종목별 당일 공시 — board/ingest/dart.disclosures_for · kind_of."""
import unittest
from unittest import mock

from ..ingest import dart as D
from ..ingest.http import Fetch


class KindOf(unittest.TestCase):
    def test_mapping(self):
        cases = {
            '단일판매ㆍ공급계약체결': 'contract',
            '자기주식취득 신탁계약 체결 결정': 'buyback',      # contract 보다 먼저
            '유상증자결정': 'capital',
            '주요사항보고서(무상증자결정)': 'capital',
            '최대주주변경': 'owner',
            '임원ㆍ주요주주특정증권등소유상황보고서': 'owner',
            '투자판단관련주요경영사항 (임상 3상 승인)': 'clinical',
            '연결재무제표기준영업(잠정)실적(공정공시)': 'earnings',
            '조회공시요구(현저한시황변동)에 대한 답변': 'inquiry',
            '기타경영사항(자율공시)': 'other',
            '': 'other',
            None: 'other',
        }
        for title, kind in cases.items():
            self.assertEqual(D.kind_of(title), kind, title)


class DisclosuresFor(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self.reply = {'status': '000', 'list': [
            {'report_nm': '단일판매ㆍ공급계약체결', 'rcept_no': '20260921000123',
             'rcept_dt': '20260921', 'flr_nm': '케이씨', 'stock_code': '029460'},
            {'report_nm': '', 'rcept_no': 'x', 'rcept_dt': '20260921'},
        ]}

        def fake_get(s, url, params=None, timeout=None, retries=None, **kw):
            self.seen.append(dict(url=url, params=params, timeout=timeout, retries=retries))
            return self.reply
        self.patches = [
            mock.patch.object(D, 'corp_codes', return_value={'029460': '00126380'}),
            mock.patch.object(D, '_key', return_value='K'),
            mock.patch.object(D, 'session', return_value=None),
            mock.patch.object(D, 'get', side_effect=fake_get),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_asks_by_corp_code_for_the_one_day(self):
        got = D.disclosures_for('029460', '2026-09-21', timeout=8, retries=2)
        p = self.seen[0]['params']
        self.assertEqual(p['corp_code'], '00126380')
        self.assertEqual((p['bgn_de'], p['end_de']), ('20260921', '20260921'))
        self.assertNotIn('corp_cls', p, '시장 구분으로 거르지 않는다 — 코스닥이 빠진다')
        self.assertEqual((self.seen[0]['timeout'], self.seen[0]['retries']), (8, 2))
        self.assertEqual(len(got), 1)
        r = got[0]
        self.assertEqual(r['title'], '단일판매ㆍ공급계약체결')
        self.assertEqual(r['kind'], 'contract')
        self.assertEqual(r['publisher'], 'DART')
        self.assertEqual(r['date'], '2026-09-21')
        self.assertEqual(r['link'], 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260921000123')
        self.assertEqual(r['filer'], '케이씨')

    def test_default_timeout_is_not_forced(self):
        D.disclosures_for('029460', '2026-09-21')
        self.assertIsNone(self.seen[0]['timeout'])
        self.assertIsNone(self.seen[0]['retries'])

    def test_no_result_status_is_an_empty_list(self):
        self.reply = {'status': '013', 'message': '조회된 데이타가 없습니다.'}
        self.assertEqual(D.disclosures_for('029460', '2026-09-21'), [])

    def test_error_status_raises(self):
        self.reply = {'status': '020', 'message': '요청 제한을 초과하였습니다.'}
        with self.assertRaises(Fetch) as e:
            D.disclosures_for('029460', '2026-09-21')
        self.assertIn('020', str(e.exception))

    def test_unknown_code_raises_not_empty(self):
        with self.assertRaises(Fetch) as e:
            D.disclosures_for('000000', '2026-09-21')
        self.assertIn('corp_code', str(e.exception))
        self.assertEqual(self.seen, [])

    def test_legacy_disclosures_docstring_states_its_limits(self):
        doc = D.disclosures.__doc__
        self.assertIn("corp_cls='Y'", doc)
        self.assertIn('disclosures_for', doc)


if __name__ == '__main__':
    unittest.main()
