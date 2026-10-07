"""
재무 매핑 시험 — board 재무 캐시 → 화면의 `fin`.

수집 자체는 board/ingest/financials.py 가 하고 그쪽 시험이 이미 있다
(board/tests/test_financials.py). 여기서는 **화면 계약으로 옮기는 부분만** 본다.

계약(배포본에서 확인):
    fin.byCode[code] = {fs, a: {연도: [매출, 영업이익, 순이익]},
                        q: {cur, yoy, qoq}}
"""
import unittest

from .. import financials as FIN


def corp(fs='CFS', annual=(), quarterly=()):
    return {
        'fs_div': fs,
        'annual': [dict(year=y, rev=r, op=o, ni=n) for y, r, o, n in annual],
        'quarterly': [dict(year=y, q=q, label=f'{q}{str(y)[2:]}', rev=r, op=o, ni=n)
                      for y, q, r, o, n in quarterly],
    }


class 매핑(unittest.TestCase):

    def test_연간과_분기를_계약대로_옮긴다(self):
        by = {'005930': corp(
            annual=[(2023, 100.0, 10.0, 8.0), (2024, 200.0, 20.0, 16.0),
                    (2025, 300.0, 30.0, 24.0)],
            quarterly=[(2025, '2Q', 50.0, 5.0, 4.0), (2026, '1Q', 70.0, 7.0, 6.0),
                       (2026, '2Q', 80.0, 8.0, 7.0)])}
        fin = FIN.build(by)
        self.assertEqual(fin['unit'], '억원')
        self.assertEqual(fin['years'], [2023, 2024, 2025])
        self.assertEqual(fin['quarter']['label'], '2Q26')
        self.assertEqual(fin['prevQuarter']['label'], '1Q26')

        rec = fin['byCode']['005930']
        self.assertEqual(rec['fs'], 'CFS')
        self.assertEqual(rec['a']['2025'], [300.0, 30.0, 24.0])
        self.assertEqual(rec['q']['cur'], [80.0, 8.0, 7.0])
        self.assertEqual(rec['q']['yoy'], [50.0, 5.0, 4.0], '전년 동기 3개월')
        self.assertEqual(rec['q']['qoq'], [70.0, 7.0, 6.0], '직전 분기 3개월')

    def test_1분기의_직전분기는_작년_4분기다(self):
        by = {'A': corp(quarterly=[(2025, '4Q', 10.0, 1.0, 1.0),
                                   (2026, '1Q', 20.0, 2.0, 2.0)])}
        fin = FIN.build(by)
        self.assertEqual(fin['quarter']['label'], '1Q26')
        self.assertEqual(fin['prevQuarter']['label'], '4Q25')
        self.assertEqual(fin['byCode']['A']['q']['qoq'], [10.0, 1.0, 1.0])

    def test_한_회사가_일찍_낸_분기를_기준으로_잡지_않는다(self):
        """그러면 나머지 전부가 '실적 없음' 이 된다."""
        by = {f'{i:06d}': corp(quarterly=[(2026, '2Q', 10.0, 1.0, 1.0)])
              for i in range(20)}
        by['999999'] = corp(quarterly=[(2026, '3Q', 10.0, 1.0, 1.0)])
        fin = FIN.build(by)
        self.assertEqual(fin['quarter']['label'], '2Q26')

    def test_분기가_없으면_q키를_넣지_않는다(self):
        """화면이 rec.q 로 갈라 '분기 실적 없음' 을 찍는다. 0 으로 채우면 0원이 된다."""
        by = {'A': corp(annual=[(2025, 1.0, 1.0, 1.0)]),
              'B': corp(quarterly=[(2025, '2Q', 1.0, 1.0, 1.0)])}
        fin = FIN.build(by)
        self.assertNotIn('q', fin['byCode']['A'])
        self.assertIn('q', fin['byCode']['B'])

    def test_재무가_하나도_없는_종목은_넣지_않는다(self):
        by = {'A': corp(), 'B': corp(annual=[(2025, 1.0, 1.0, 1.0)])}
        fin = FIN.build(by)
        self.assertNotIn('A', fin['byCode'])
        self.assertIn('B', fin['byCode'])

    def test_분기를_아무도_안_내도_연간만으로_카드가_뜬다(self):
        """연간만 있는데 None 을 돌려주면 카드 전체가 '미적용' 이 된다.

        화면은 F.quarter.label 을 '… 분기 실적 없음' 문구에 쓰므로 그 자리도
        채워야 한다 — 실제로 있는 연도에서 뽑는다.
        """
        by = {'A': corp(annual=[(2024, 1.0, 1.0, 1.0), (2025, 2.0, 2.0, 2.0)])}
        fin = FIN.build(by)
        self.assertIsNotNone(fin)
        self.assertEqual(fin['years'], [2024, 2025])
        self.assertEqual(fin['quarter']['label'], '4Q25')
        self.assertNotIn('q', fin['byCode']['A'])

    def test_받은게_없으면_빈dict가_아니라_None(self):
        """{} 는 자바스크립트에서 참이라 가드를 통과한 뒤 S.fin.years 에서 죽는다."""
        self.assertIsNone(FIN.build({}))
        self.assertIsNone(FIN.build({'A': corp()}))

    def test_값이_전부_None인_분기는_없는_것으로_친다(self):
        by = {'A': corp(quarterly=[(2026, '1Q', None, None, None),
                                   (2025, '4Q', 5.0, 1.0, 1.0)])}
        fin = FIN.build(by)
        self.assertEqual(fin['quarter']['label'], '4Q25')

    def test_별도기준도_그대로_남긴다(self):
        by = {'A': corp(fs='OFS', annual=[(2025, 1.0, 1.0, 1.0)])}
        self.assertEqual(FIN.build(by)['byCode']['A']['fs'], 'OFS')

    def test_일부값만_있어도_자리를_지킨다(self):
        """은행·보험은 주요계정에 매출액이 없다. 영업이익만 있어도 카드는 떠야 한다."""
        by = {'A': corp(annual=[(2025, None, 10.0, 8.0)])}
        self.assertEqual(FIN.build(by)['byCode']['A']['a']['2025'], [None, 10.0, 8.0])


if __name__ == '__main__':
    unittest.main()
