"""KBJ P2 다리 시험 — board 의 KIS·KRX·DART 호출이 KBJ 브리지로만 간다(설계 §1.9·§3.8 K3).

발급하지 않는다: `kis.token()` 은 KBJ auth 가 Redis 에 둔 토큰을 읽기만 하고, 없으면 사유를 담은
Fetch 다(발급 POST 없음). 주소는 논리 URL 이고, 브리지는 http(s) 주소를 받지 않는다.
"""
import unittest

from kbj.config.settings import Settings
from kbj.data import legacy_bridge

from ..ingest import dart, kis, krx
from ..ingest.http import Fetch


class KbjBridgeTest(unittest.TestCase):
    def setUp(self):
        legacy_bridge.reset()
        # Redis·키 없는 KBJ 설정 — 실제 .env 를 읽지 않는다
        legacy_bridge.configure(settings=Settings(_env_file=None))

    def tearDown(self):
        legacy_bridge.reset()

    def test_board_reads_tokens_only_and_calls_logical_urls(self):
        self.assertEqual((kis.base(), krx.BASE, dart.BASE), ('kis:', 'krx:', 'dart:'))
        self.assertIs(kis.session, legacy_bridge.session)
        for gone in ('REAL', 'VTS', 'TOKEN_CACHE', '_cached_token', '_save_token'):
            self.assertFalse(hasattr(kis, gone), gone)
        with self.assertRaises(Fetch) as e:
            kis.token()                           # 발급하지 않는다 — auth 를 기다린다
        self.assertIn('auth', str(e.exception))
        h = kis._headers('FHKST01010900')
        self.assertNotIn('authorization', {k.lower() for k in h})
        self.assertNotIn('appkey', {k.lower() for k in h})
        # 브리지는 직접 주소를 받지 않는다 — 남은 직접 호출이 조용히 새지 않는다
        with self.assertRaises(ValueError):
            kis.session().get('https://example.invalid/uapi/x')
        with self.assertRaises(ValueError):
            kis.session().post('kis:/oauth2/x')


if __name__ == '__main__':
    unittest.main()
