"""
업종 묶음 시험.

공공데이터포털에는 업종 필드가 없고 DART 는 `induty_code`(KSIC 숫자)만 준다 —
**이름은 안 준다.** 그래서 배포본에서 뽑은 업종명·배정을 지식으로 쓰고,
그 배정과 DART 코드를 조인해 'induty_code → 업종명' 표를 학습한다.

[KBJ P1] knowledge/industries.json 은 원본(제3자 배포본 추출) 대신 같은 규모
(업종 58개·배정 500건 이상·중복 없음)의 합성 사전이다(tests/fixtures/make_synthetic.py).
"""
import unittest

from .. import industries as IND


class 지식(unittest.TestCase):

    def test_배포본에서_뽑은_업종이_실려있다(self):
        k = IND.load_knowledge()
        self.assertEqual(len(k['sectors']), 58)
        n = sum(len(s['members']) for s in k['sectors'])
        self.assertGreater(n, 500)

    def test_한_종목이_한_업종에만_든다(self):
        """테마는 다대다지만 업종은 하나다. 겹치면 합계가 부풀어 오른다."""
        seen = set()
        for s in IND.load_knowledge()['sectors']:
            for c in s['members']:
                self.assertNotIn(c, seen, c)
                seen.add(c)


class KSIC학습(unittest.TestCase):

    def test_배정과_코드를_조인해_표를_만든다(self):
        known = {'A': '반도체 제조업', 'B': '반도체 제조업', 'C': '의약품 제조업'}
        induty = {'A': '26110', 'B': '26110', 'C': '21102'}
        table, shaky = IND.learn_ksic(known, induty)
        self.assertEqual(table['26110'], '반도체 제조업')
        self.assertEqual(table['21102'], '의약품 제조업')
        self.assertEqual(shaky, [])

    def test_이름이_갈리면_최빈을_쓰되_애매하면_뺀다(self):
        # 4:1 → 80% 로 채택
        known = {f'{i}': ('가' if i < 4 else '나') for i in range(5)}
        table, shaky = IND.learn_ksic(known, {f'{i}': '100' for i in range(5)})
        self.assertEqual(table['100'], '가')
        self.assertEqual(shaky, [])

        # 3:2 → 60% 로 기권. 틀린 업종은 없는 업종보다 나쁘다.
        known = {f'{i}': ('가' if i < 3 else '나') for i in range(5)}
        table, shaky = IND.learn_ksic(known, {f'{i}': '200' for i in range(5)})
        self.assertNotIn('200', table)
        self.assertEqual(shaky, ['200'])

    def test_코드가_없는_종목은_학습에_안_쓴다(self):
        table, _ = IND.learn_ksic({'A': '가'}, {})
        self.assertEqual(table, {})


class 배정(unittest.TestCase):

    def test_지식에_있으면_그대로(self):
        k = IND.load_knowledge()
        code = k['sectors'][0]['members'][0]
        got = IND.assign([code], k)
        self.assertEqual(got[code], k['sectors'][0]['name'])

    def test_지식에_없으면_학습표로_채운다(self):
        k = IND.load_knowledge()
        got = IND.assign(['999999'], k, ksic={'26110': '반도체 제조업'},
                         induty_by_stock={'999999': '26110'})
        self.assertEqual(got['999999'], '반도체 제조업')

    def test_모르면_키_자체를_안_넣는다(self):
        """억지로 '기타' 에 몰면 그 칸이 실제 업종인 것처럼 보인다."""
        got = IND.assign(['999999'], IND.load_knowledge())
        self.assertNotIn('999999', got)

    def test_요청한_종목만_돌려준다(self):
        k = IND.load_knowledge()
        code = k['sectors'][0]['members'][0]
        got = IND.assign([code], k)
        self.assertEqual(list(got), [code])


if __name__ == '__main__':
    unittest.main()
