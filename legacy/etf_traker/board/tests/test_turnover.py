"""거래대금 0 — '거래 없음'과 '소스가 안 줌'을 가른다.

이 구분을 놓치면 유동성 하한에 전 종목이 걸려 랭킹 표 네 장이 통째로 빈다.
2026-08-27 과 2026-08-28 실행에서 연달아 그랬다.
"""
import unittest



def rows(close=10000, volume=1000):
    return [dict(asof='2026-08-28', open=close, high=close, low=close,
                 close=close, volume=volume)]


class NaverUniverseTest(unittest.TestCase):

    def test_목록API의_0을_None으로_바꾼다(self):
        from ..ingest import naver
        import inspect
        src = inspect.getsource(naver.fetch_universe)
        self.assertIn('if tv == 0 and vol:', src)
        self.assertIn('tv = None', src)
