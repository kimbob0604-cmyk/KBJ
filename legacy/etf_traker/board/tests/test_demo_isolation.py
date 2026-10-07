"""--demo 가 실제 사이트를 덮으면 안 된다.

DB 는 `.demo` 로 나눠 놨으면서 사이트는 docs/ 를 그대로 썼다. 데모를 한 번
돌리면 그날 만든 진짜 보드가 합성 데이터로 덮이고, 보관함에 합성 날짜가 끼어
날짜 선택기에 실제 날짜와 나란히 남았다. docs/d/2025-02-24.html 이 실제로
그렇게 레포에 들어가 있었다.
"""
import inspect
import json
import os
import unittest

from .. import run as R


class DemoSiteTest(unittest.TestCase):

    def test_demo_site_is_not_the_real_site(self):
        self.assertNotEqual(os.path.abspath(R.DEMO_SITE),
                            os.path.abspath(R.SITE))

    def test_demo_site_is_not_inside_the_real_site(self):
        # docs/demo/ 로 두면 보관 목록·발행에 함께 딸려 간다.
        real = os.path.abspath(R.SITE) + os.sep
        self.assertFalse(os.path.abspath(R.DEMO_SITE).startswith(real))

    def test_cmd_demo_passes_the_demo_site(self):
        # 소스를 본다. 이번 사고가 "DB 만 나누고 사이트는 안 나눔" 이었다.
        src = inspect.getsource(R.cmd_demo)
        self.assertIn('DEMO_SITE', src)
        self.assertNotIn('demo.main(DB_PATH + \'.demo\', SITE', src)

    def test_serve_accepts_a_site(self):
        sig = inspect.signature(R.cmd_serve)
        self.assertIn('site', sig.parameters)

    def test_published_archive_keeps_only_recent_dates(self):
        """발행된 보관 목록에 동떨어진 날짜가 남아 있지 않은지.

        site.publish 는 최신 KEEP_DAYS 일만 남긴다고 약속한다. 데모가 새어
        들어가면 합성 구간(실데이터보다 1년 넘게 과거)이 그 약속을 깬다.
        연도를 박으면 해가 바뀔 때 시험이 깨지므로 규약으로 본다.
        """
        from datetime import date
        from ..web.site import KEEP_DAYS
        idx = os.path.join(R.SITE, 'd', 'index.json')
        if not os.path.exists(idx):
            self.skipTest('아직 발행된 사이트가 없다')
        with open(idx, encoding='utf-8') as f:
            dates = sorted(json.load(f).get('dates') or [], reverse=True)
        if not dates:
            self.skipTest('보관된 날짜가 없다')
        newest = date.fromisoformat(dates[0])
        stray = [d for d in dates
                 if (newest - date.fromisoformat(d)).days > KEEP_DAYS]
        self.assertEqual(stray, [],
                         f'보관 목록에 {KEEP_DAYS}일보다 오래된 날짜가 있다: {stray}')


if __name__ == '__main__':
    unittest.main()


class TimestampTest(unittest.TestCase):
    """DB 에 남기는 시각은 오프셋 붙은 KST 하나로 통일한다.

    sector_map.updated_at 을 pipeline 은 KST 로, classify 와 restore_sectors 는
    naive datetime.now() 로 썼다. 러너는 UTC 라 9시간 어긋나고, naive 쪽은
    오프셋이 없어 사후에 어느 쪽인지 구분조차 안 된다.
    """

    def test_now_kst_carries_an_offset(self):
        from ..engine.db import now_kst
        t = now_kst()
        self.assertTrue(t.endswith('+09:00'), t)

    def test_no_naive_now_in_package(self):
        """`datetime.now()` 를 인자 없이 부르는 곳이 없어야 한다.

        문자열 검색이 아니라 AST 로 본다. 첫 판은 문자열로 훑다가 이 사고를
        설명하는 독스트링 자체에 걸렸다 — 산문과 호출은 다르다.
        """
        import ast
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        bad = []
        for f in sorted(root.rglob('*.py')):
            if 'tests' in f.parts:
                continue
            tree = ast.parse(f.read_text(encoding='utf-8'), str(f))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'now'
                        and not node.args and not node.keywords):
                    bad.append(f'{f.relative_to(root)}:{node.lineno}')
        self.assertEqual(bad, [], f'tz 없는 .now(): {bad}')
