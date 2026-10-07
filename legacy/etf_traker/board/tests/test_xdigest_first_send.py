"""2026-09-22 첫 실발송이 드러낸 것들.

러너에서 전 경로를 처음 돌려 텔레그램 3통이 실제로 나갔다(13:26 KST). 시험은
전부 통과하고 있었는데 실물에서 셋이 드러났다 — 시험이 스텁으로만 돌던 자리다.

1. ① 배정 모델(`claude-haiku-4-5`)이 `output_config.effort` 를 400 으로 거절해
   분석이 통째로 죽었다. 그날 다이제스트는 사실 한 줄 없이 결손만 싣고 나갔다.
2. 그 400 의 **원문**이 텔레그램 첫 줄에 실렸다 — JSON 과 `request_id` 까지.
3. 결손 경로의 계정 목록이 **152줄**이었다(187건이 152개 계정으로 흩어졌고
   1건짜리가 130개). 휴대폰에서 다 넘어가지 않는다.
"""
import unittest

from ..writer import claude as C
from ..xdigest import render as R

# 실제로 올라온 예외 문자열. 손으로 지어내지 않는다.
REAL_400 = ("BadRequestError: Error code: 400 - {'type': 'error', 'error': "
            "{'type': 'invalid_request_error', 'message': 'This model does not "
            "support the effort parameter.'}, 'request_id': "
            "'req_011CfHqvwCaBQmdNioFkvm1v'}")


def _lines(text):
    """`_fallback` 은 통짜 문자열을 돌려준다 — 줄로 갈라 본다."""
    return text.split('\n')


class Boom(Exception):
    def __init__(self, msg, status_code=400):
        super().__init__(msg)
        self.status_code = status_code


class FakeClient:
    """`messages.create` 한 번의 호출 인자를 남기고 정해진 대로 반응한다."""

    def __init__(self, refuse_effort=False):
        self.calls, self.refuse_effort = [], refuse_effort
        self.messages = self

    def create(self, **kw):
        # **사본**을 담는다. 같은 dict 를 두 번 담으면 두 번째 호출이 effort 를
        # 빼는 순간 첫 호출 기록까지 바뀌어, 붙였다 뺀 것과 처음부터 안 붙인 것이
        # 시험에서 같아진다.
        self.calls.append({k: (dict(v) if isinstance(v, dict) else v)
                           for k, v in kw.items()})
        if self.refuse_effort and 'effort' in (kw.get('output_config') or {}):
            raise Boom('Error code: 400 - This model does not support the '
                       'effort parameter.')
        return _Resp()


class _Resp:
    stop_reason = 'end_turn'
    usage = None
    content = [type('B', (), {'type': 'text', 'text': '{}'})()]


CFG = {'writer': {'models': {'narrate': 'claude-sonnet-5',
                             'cluster': 'claude-haiku-4-5'},
                  'effort': 'medium',
                  'no_effort_models': ['claude-haiku-4-5']}}


class EffortIsNotSentToModelsThatRefuseIt(unittest.TestCase):
    def setUp(self):
        C.NO_EFFORT.clear()

    def test_설정에_적힌_모델에는_붙이지_않는다(self):
        cl = FakeClient()
        C.ask(cl, CFG, 'sys', 'p', model='claude-haiku-4-5')
        self.assertNotIn('effort', cl.calls[0].get('output_config') or {})
        self.assertEqual(len(cl.calls), 1, '헛된 400 을 한 번 맞고 오면 안 된다')

    def test_다른_모델에는_그대로_붙인다(self):
        cl = FakeClient()
        C.ask(cl, CFG, 'sys', 'p')
        self.assertEqual(cl.calls[0]['output_config']['effort'], 'medium')

    def test_설정에_없던_모델이_거절하면_빼고_다시_부른다(self):
        """이름 규칙으로 짐작하지 않는다 — 모르는 모델은 실측으로 배운다."""
        cfg = {'writer': dict(CFG['writer'], no_effort_models=[])}
        cl = FakeClient(refuse_effort=True)
        C.ask(cl, cfg, 'sys', 'p', model='claude-haiku-4-5')
        self.assertEqual(len(cl.calls), 2)
        self.assertIn('effort', cl.calls[0]['output_config'])
        self.assertNotIn('effort', cl.calls[1].get('output_config') or {})
        self.assertIn('claude-haiku-4-5', C.NO_EFFORT)

    def test_한_번_배우면_그_프로세스에서_다시_안_붙인다(self):
        cfg = {'writer': dict(CFG['writer'], no_effort_models=[])}
        cl = FakeClient(refuse_effort=True)
        C.ask(cl, cfg, 'sys', 'p', model='claude-haiku-4-5')
        cl.calls.clear()
        C.ask(cl, cfg, 'sys', 'p', model='claude-haiku-4-5')
        self.assertEqual(len(cl.calls), 1)

    def test_effort_가_아닌_400_은_가리지_않는다(self):
        """다른 400 을 이것으로 오인하면 진짜 오류를 삼킨다."""
        cfg = {'writer': dict(CFG['writer'], no_effort_models=[])}

        class Other(FakeClient):
            def create(self, **kw):
                self.calls.append(kw)
                raise Boom('Error code: 400 - max_tokens is too large')

        cl = Other()
        with self.assertRaises(Boom):
            C.ask(cl, cfg, 'sys', 'p', model='claude-haiku-4-5')
        self.assertEqual(len(cl.calls), 1, '재시도하면 안 된다')

    def test_400_이_아닌_오류는_그대로_올린다(self):
        cfg = {'writer': dict(CFG['writer'], no_effort_models=[])}

        class Dead(FakeClient):
            def create(self, **kw):
                self.calls.append(kw)
                raise Boom('effort', status_code=500)

        with self.assertRaises(Boom):
            C.ask(Dead(), cfg, 'sys', 'p', model='claude-haiku-4-5')


class ReasonIsReadable(unittest.TestCase):
    def test_API_원문에서_뜻있는_조각만_남긴다(self):
        out = R._short_why(REAL_400)
        self.assertIn('does not support the effort parameter', out)
        self.assertNotIn('request_id', out)
        self.assertNotIn('{', out)
        self.assertIn('BadRequestError', out, '예외 이름은 남긴다')

    def test_평범한_사유는_건드리지_않는다(self):
        self.assertEqual(R._short_why('ConnectTimeout: api.anthropic.com'),
                         'ConnectTimeout: api.anthropic.com')

    def test_긴_사유는_자르고_표시를_남긴다(self):
        out = R._short_why('가' * 300)
        self.assertLessEqual(len(out), R.WHY_MAX)
        self.assertTrue(out.endswith('…'))

    def test_빈_사유도_말이_된다(self):
        self.assertEqual(R._short_why(None), '사유 미기록')

    def test_줄바꿈은_한_줄로_눌린다(self):
        self.assertEqual(R._short_why('앞\n\n뒤'), '앞 뒤')


class FallbackListsAreReadableOnAPhone(unittest.TestCase):
    """계정 152줄이 나간 자리."""

    def posts(self, n_accounts, per=1):
        ps = []
        for a in range(n_accounts):
            for k in range(per):
                ps.append(dict(id=f'at://x/{a}-{k}', account=f'a{a:03d}.bsky.social',
                               url=f'https://bsky.app/profile/a{a:03d}/post/{k}',
                               posted_at='2026-09-22T05:00:00+09:00',
                               text='t', in_window=True))
        return dict(asof='2026-09-22', posts=ps,
                    sources={'bluesky': {'blocked': 0, 'cut': None}},
                    coverage={'first': None, 'last': None, 'gaps': []})

    def test_계정_목록에_상한이_있다(self):
        lines = _lines(R._fallback({'error': REAL_400}, self.posts(152), {}, []))
        acct = [l for l in lines if l.startswith('• @')]
        self.assertLessEqual(len(acct), R.FALLBACK_ACCT_MAX)

    def test_자른_계정의_수와_건수를_적는다(self):
        lines = _lines(R._fallback({'error': REAL_400}, self.posts(152), {}, []))
        tail = [l for l in lines if l.startswith('• 외 ') and '개 계정' in l]
        self.assertEqual(len(tail), 1)
        self.assertIn(f'{152 - R.FALLBACK_ACCT_MAX}개 계정', tail[0])

    def test_상한_아래면_꼬리표가_없다(self):
        lines = _lines(R._fallback({'error': REAL_400}, self.posts(3), {}, []))
        self.assertEqual([l for l in lines if '개 계정' in l], [])

    def test_건수_많은_계정이_남는다(self):
        """자를 때 아무거나 버리지 않는다."""
        p = self.posts(20)
        # id 는 서로 달라야 한다 — `index()` 가 id 로 모으므로 같은 id 9건은
        # 1건이 된다(그래서 처음 쓴 시험이 9건이 아니라 1건을 세고 있었다).
        p['posts'] += [dict(id=f'at://big/{k}', account='big.bsky.social',
                            url=f'https://bsky.app/profile/big/post/{k}',
                            posted_at='2026-09-22T06:00:00+09:00',
                            text='t', in_window=True) for k in range(9)]
        lines = _lines(R._fallback({'error': REAL_400}, p, {}, []))
        acct = [l for l in lines if l.startswith('• @')]
        self.assertIn('@big.bsky.social', acct[0])

    def test_결손_줄에_request_id_가_없다(self):
        lines = _lines(R._fallback({'error': REAL_400}, self.posts(3), {}, []))
        self.assertNotIn('request_id', '\n'.join(lines))

    def test_URL_목록은_그대로_있다(self):
        """읽는 사람이 직접 가 볼 곳이라 이 경로의 값이다."""
        lines = _lines(R._fallback({'error': REAL_400}, self.posts(5), {}, []))
        self.assertTrue([l for l in lines if l.startswith('• https://bsky.app/')])


if __name__ == '__main__':
    unittest.main()
