"""
종목별 재료의 소비자 — payload(실시간 보드)·render(구운 표)·run(단계 실패 시 파일).

  - payload: achieved 행에 trigger(title, publisher, link, source) 를 붙이고
    notices.miss 에 triggers.missing 을 합류한다. 링크는 **여기에만** 있다
  - render: 이름 옆 '재료 N' 표식 + 툴팁. 재료 없는 행에는 아무것도 없다.
    배너의 결손 합류는 payload 와 같은 곳(render.notices)에서 한다
  - app.js: 신고가 행 아래 재료 줄(제목·매체·링크). 텔레그램이 '전체는 대시보드' 라
    적는 그 전체가 여기다
  - run.cmd_triggers: 예외가 나도 triggers.json 은 남는다(사유 포함)
"""
import unittest
from unittest import mock

from ..ingest import triggers as TR
from ..web import payload as PL
from ..web import render as R

NH = dict(labels={}, counts={}, achieved=[
    dict(code='029460', name='케이씨', chg_pct=5.43, turnover=100.0, mktcap=2000.0,
         close_basis=dict(label='w52'), high_basis=dict(label='w52')),
    dict(code='000500', name='가온전선', chg_pct=-0.31, turnover=1106.0, mktcap=97075.0,
         close_basis=dict(label='hist'), high_basis=dict(label='d120')),
], proximity=[], as_of='2026-09-21', basis='close',
    thresholds=dict(proximity=dict(max_gap_pct=5.0, min_mktcap_eok=1000.0, narrow_days=5),
                    lookback_trading_days=dict(d120=120), lookback_calendar_days=dict(w52=364)))
TRIG = dict(source='triggers', as_of='2026-09-21', collected_at='2026-09-21T17:05:00+09:00',
            by_code={
                '029460': dict(code='029460', name='케이씨', items=[
                    dict(title='단일판매ㆍ공급계약체결', link='https://dart.fss.or.kr/x?rcpNo=1',
                         publisher='DART', published_at='2026-09-21', source='dart',
                         kind='contract', matched_by='corp_code'),
                    dict(title='케이씨 "장비" <수주>', link='https://mk.co.kr/1', publisher='매일경제',
                         published_at='2026-09-21T09:10+09:00', source='naver_news',
                         matched_by='title')]),
                '000500': dict(code='000500', name='가온전선', items=[])},
            sources={}, missing=['X 포워딩 인박스 없음 — 봇에 게시물을 공유하면 붙는다'])


class Payload(unittest.TestCase):
    def _build(self, triggers):
        return PL.build(NH, {}, {}, {}, {}, meta={}, triggers=triggers)

    def test_achieved_row_carries_triggers_with_links(self):
        p = self._build(TRIG)
        by = {x['code']: x for x in p['achieved']}
        t = by['029460']['trigger']
        self.assertEqual(len(t), 2)
        self.assertEqual(t[0], dict(title='단일판매ㆍ공급계약체결', publisher='DART',
                                    link='https://dart.fss.or.kr/x?rcpNo=1', source='dart',
                                    date='2026-09-21', kind='contract'))
        self.assertEqual(t[1]['link'], 'https://mk.co.kr/1')
        self.assertNotIn('trigger', by['000500'], '재료 없는 종목은 키 자체가 없다')

    def test_trigger_missing_joins_notices(self):
        p = self._build(TRIG)
        self.assertIn('X 포워딩 인박스 없음 — 봇에 게시물을 공유하면 붙는다', p['notices']['miss'])

    def test_without_triggers_nothing_is_added(self):
        p = self._build(None)
        self.assertFalse(any('trigger' in x for x in p['achieved']))
        self.assertFalse(any('재료' in m for m in p['notices']['miss']))

    def test_absent_standin_is_a_banner_line(self):
        p = self._build(TR.absent('2026-09-21'))
        self.assertIn(TR.ABSENT_LINE, p['notices']['miss'])

    def test_from_state_reads_triggers(self):
        import inspect
        self.assertIn("triggers=TR.load(asof)", inspect.getsource(PL.from_state))


class Render(unittest.TestCase):
    def _doc(self, triggers):
        return R.build(NH, {}, {}, {}, {}, meta={}, triggers=triggers)

    def test_tag_with_tooltip_on_the_stock_with_items(self):
        doc = self._doc(TRIG)
        self.assertEqual(doc.count('tag-trig'), 1)
        self.assertIn('재료 2</span>', doc)
        self.assertIn('DART: 단일판매ㆍ공급계약체결', doc)
        # 제목의 따옴표·꺾쇠는 속성 안에서 이스케이프된다
        self.assertIn('&quot;장비&quot; &lt;수주&gt;', doc)
        self.assertNotIn('"장비"', doc.split('tag-trig')[1][:200])

    def test_no_tag_without_triggers(self):
        self.assertNotIn('tag-trig', self._doc(None))
        self.assertNotIn('tag-trig', self._doc(TR.absent('2026-09-21')))

    def test_from_state_reads_triggers(self):
        # payload 와 같은 입력 — 파일이 없으면 '수집되지 않음' 대역이라야 두 배너가 같다.
        import inspect
        self.assertIn("triggers=TR.load(asof)", inspect.getsource(R.from_state))

    def test_notices_join_trigger_missing_for_the_baked_board(self):
        miss, _ = R.notices(NH, {}, {}, {}, {}, TRIG)
        self.assertIn('X 포워딩 인박스 없음 — 봇에 게시물을 공유하면 붙는다', miss)
        miss, _ = R.notices(NH, {}, {}, {}, {}, TR.absent('2026-09-21'))
        self.assertIn(TR.ABSENT_LINE, miss)
        miss, _ = R.notices(NH, {}, {}, {}, {})
        self.assertEqual(miss, [])
        doc = self._doc(TRIG)
        self.assertIn('X 포워딩 인박스 없음', doc)


class LiveBoard(unittest.TestCase):
    """app.js 가 payload 의 achieved[].trigger 를 그린다 — 링크는 여기에만 있다."""

    def test_app_js_renders_trigger_rows_with_links(self):
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'app.js')
        with open(p, encoding='utf-8') as f:
            js = f.read()
        self.assertIn('x.trigger', js)
        self.assertIn('trig-row', js)
        self.assertIn("dart: '공시'", js)
        self.assertIn('esc(t.link)', js)
        css = os.path.join(os.path.dirname(p), 'app.css')
        with open(css, encoding='utf-8') as f:
            self.assertIn('.trig-row', f.read())


class RunStage(unittest.TestCase):
    """run.cmd_triggers — 실패해도 파일은 남는다."""

    def setUp(self):
        from .. import run as RUN
        self.RUN = RUN
        self.written = {}

        def fake_write(asof, name, payload):
            self.written[name] = payload
            return name
        self.patches = [
            mock.patch.object(RUN.B, 'write', side_effect=fake_write),
            mock.patch.object(RUN, '_asof', return_value='2026-09-21'),
            mock.patch.object(RUN, 'log', lambda *a: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_missing_newhigh_leaves_a_failed_file(self):
        with mock.patch.object(self.RUN.B, 'read', return_value=None):
            rc = self.RUN.cmd_triggers()
        self.assertEqual(rc, 1)
        out = self.written['triggers.json']
        self.assertEqual(out['by_code'], {})
        self.assertTrue(all(v['cut'].startswith('단계 실패') for v in out['sources'].values()))
        self.assertEqual(len(out['missing']), 1)
        self.assertIn('newhigh.json', out['missing'][0])
        self.assertIn('generated_at', out)

    def test_collect_exception_leaves_a_failed_file(self):
        with mock.patch.object(self.RUN.B, 'read', return_value=dict(achieved=[])), \
             mock.patch.object(TR, 'collect', side_effect=RuntimeError('boom')):
            rc = self.RUN.cmd_triggers()
        self.assertEqual(rc, 1)
        self.assertIn('boom', self.written['triggers.json']['missing'][0])

    def test_success_writes_the_collect_output(self):
        got = dict(source='triggers', by_code={}, missing=[], sources={})
        with mock.patch.object(self.RUN.B, 'read', return_value=dict(achieved=[])), \
             mock.patch.object(TR, 'collect', return_value=got):
            rc = self.RUN.cmd_triggers('2026-09-21')
        self.assertEqual(rc, 0)
        self.assertEqual(self.written['triggers.json']['source'], 'triggers')
        self.assertIn('generated_at', self.written['triggers.json'])

    def test_daily_calls_triggers_between_news_and_write(self):
        import inspect
        src = inspect.getsource(self.RUN.cmd_daily)
        self.assertLess(src.index('cmd_news()'), src.index('cmd_triggers(asof)'))
        self.assertLess(src.index('cmd_triggers(asof)'), src.index('cmd_write()'))

    def test_send_passes_triggers_to_the_message(self):
        import inspect
        self.assertIn('triggers=TR.load(asof)', inspect.getsource(self.RUN.cmd_send))


if __name__ == '__main__':
    unittest.main()
