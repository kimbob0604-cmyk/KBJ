"""환율 HTML 폴백 — JSON 후보가 다 죽었을 때 마지막으로 기대는 경로."""
import unittest

from ..ingest import naver

# finance.naver.com/marketindex/ 의 실제 구조를 줄인 것.
HTML_DOWN = '''
<ul class="data_lst" id="exchangeList">
 <li class="on">
  <a href="/marketindex/exchangeDetail.naver?marketindexCd=FX_USDKRW" class="head usd">
   <h3 class="h_lst"><span class="blind">미국 USD</span></h3>
   <div class="head_info point_dn">
    <span class="value">1,384.80</span>
    <span class="txt_krw"><span class="blind">원</span></span>
    <span class="change">1.50</span>
    <span class="blind">하락</span>
   </div>
  </a>
 </li>
 <li>
  <div class="head_info point_up"><span class="value">9.99</span></div>
 </li>
</ul>
'''
HTML_UP = HTML_DOWN.replace('point_dn', 'point_up').replace('하락', '상승')


class FakeSess:
    def __init__(self, html):
        self.html = html


def _patch(html):
    """get() 만 갈아끼운다. 네트워크를 타지 않는다."""
    def fake_get(s, url, want=None, params=None, retries=None, **kw):
        return html
    return fake_get


class FxHtmlTest(unittest.TestCase):

    def setUp(self):
        self.real = naver.get

    def tearDown(self):
        naver.get = self.real

    def test_하락이면_음수다(self):
        naver.get = _patch(HTML_DOWN)
        fx = naver._fx_from_html(FakeSess(HTML_DOWN))
        self.assertEqual(fx['value'], 1384.80)
        # 전일 1386.30 → -1.50 → -0.11%
        self.assertEqual(fx['chg_pct'], -0.11)
        self.assertEqual(fx['source'], 'naver')

    def test_상승이면_양수다(self):
        naver.get = _patch(HTML_UP)
        fx = naver._fx_from_html(FakeSess(HTML_UP))
        self.assertEqual(fx['value'], 1384.80)
        # 전일 1383.30 → +1.50 → +0.11%
        self.assertEqual(fx['chg_pct'], 0.11)

    def test_첫_항목만_읽는다(self):
        """두 번째 li 의 9.99 를 집으면 안 된다."""
        naver.get = _patch(HTML_DOWN)
        self.assertEqual(naver._fx_from_html(FakeSess(HTML_DOWN))['value'], 1384.80)

    def test_구조가_바뀌면_조용히_넘어가지_않는다(self):
        naver.get = _patch('<html><body>개편했습니다</body></html>')
        with self.assertRaises(naver.Fetch):
            naver._fx_from_html(FakeSess(''))

    def test_값이_없으면_예외다(self):
        naver.get = _patch('<ul id="exchangeList"><li><span>없음</span></li></ul>')
        with self.assertRaises(naver.Fetch):
            naver._fx_from_html(FakeSess(''))
