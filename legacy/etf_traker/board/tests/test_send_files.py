"""매일 17:30 보드 HTML·랭킹 엑셀 자동 발송 (D-063)."""
import os
import unittest

from .. import run as R
from ..report import telegram as TG


class SendDocumentTest(unittest.TestCase):

    def test_missing_file_is_an_error_not_silence(self):
        ok, why = TG.send_document('/없는/파일.html', token='t', chat_id='c')
        self.assertFalse(ok)
        self.assertIn('없다', why)

    def test_missing_creds_named(self):
        ok, why = TG.send_document(__file__, token=None, chat_id=None)
        # 자격증명이 환경에 있으면 이 시험은 성립하지 않는다 — 건너뛴다.
        if os.environ.get('TELEGRAM_BOT_TOKEN'):
            self.skipTest('실자격증명이 있는 환경')
        self.assertFalse(ok)
        self.assertIn('TELEGRAM_BOT_TOKEN', why)


class SendFilesTest(unittest.TestCase):

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix='sendtest-')
        self.real_site = R.SITE
        R.SITE = self.tmp
        os.makedirs(os.path.join(self.tmp, 'd'), exist_ok=True)
        self.sent = []

    def tearDown(self):
        import shutil
        R.SITE = self.real_site
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _site(self, asof, with_xlsx=True):
        import json
        with open(os.path.join(self.tmp, 'index.html'), 'w') as f:
            f.write('<title>보드</title>')
        with open(os.path.join(self.tmp, 'd', 'index.json'), 'w') as f:
            json.dump(dict(latest=asof, dates=[asof]), f)
        if with_xlsx:
            os.makedirs(os.path.join(self.tmp, 'x'), exist_ok=True)
            with open(os.path.join(self.tmp, 'x', f'rankings-{asof}.xlsx'), 'wb') as f:
                f.write(b'xlsx')

    def _fake_tg(self):
        sent = self.sent

        class FakeTG:
            @staticmethod
            def send_document(path, caption='', **kw):
                sent.append(('doc', os.path.basename(path), caption))
                return True, os.path.basename(path)

            @staticmethod
            def send(text, **kw):
                sent.append(('msg', text, ''))
                return True, '1건 발송'
        return FakeTG

    def test_sends_both_files_with_dated_names(self):
        self._site('2026-09-01')
        rc = R._send_files('2026-09-01', self._fake_tg(), only_fresh=False)
        self.assertEqual(rc, 0)
        kinds = [(k, n) for k, n, _ in self.sent]
        self.assertIn(('doc', '신고가보드-2026-09-01.html'), kinds)
        self.assertIn(('doc', 'rankings-2026-09-01.xlsx'), kinds)

    def test_missing_xlsx_is_announced(self):
        self._site('2026-09-01', with_xlsx=False)
        rc = R._send_files('2026-09-01', self._fake_tg(), only_fresh=False)
        self.assertEqual(rc, 0)
        msgs = [t for k, t, _ in self.sent if k == 'msg']
        self.assertTrue(any('엑셀' in m for m in msgs),
                        '한 개만 보내면 원래 한 개였다고 믿는다 — 알려야 한다')

    def test_only_fresh_skips_stale_board(self):
        self._site('2020-01-02')
        rc = R._send_files('2020-01-02', self._fake_tg(), only_fresh=True)
        self.assertEqual(rc, 0)
        self.assertEqual(self.sent, [], '휴장일에는 보내지 않는다')

    def test_manual_send_ignores_freshness(self):
        self._site('2020-01-02')
        rc = R._send_files('2020-01-02', self._fake_tg(), only_fresh=False)
        self.assertEqual(rc, 0)
        self.assertTrue(self.sent)

    def test_site_asof_reads_the_archive_index(self):
        self._site('2026-09-01')
        self.assertEqual(R._site_asof(), '2026-09-01')

    # ── 무엇을 보내는가 ────────────────────────────────
    def test_sends_the_archived_board_not_the_empty_shell(self):
        """사이트 루트는 데이터가 없는 고정 셸이다. 그걸 보내면 빈 표가 간다.

        셸은 같은 사이트의 api/latest.json 을 받아야 그려지는데, 내려받은
        첨부 파일에는 그 사이트가 없다. 보관본(d/)은 데이터가 박혀 있다.
        """
        self._site('2026-09-01')
        with open(os.path.join(self.tmp, 'index.html'), 'w') as f:
            f.write('<title>셸</title><div id="body"></div>')
        with open(os.path.join(self.tmp, 'd', '2026-09-01.html'), 'w') as f:
            f.write('<title>보드</title>코스맥스 +7.07%')
        sent_paths = []

        class FakeTG:
            @staticmethod
            def send_document(path, caption='', **kw):
                with open(path, 'rb') as f:
                    sent_paths.append(f.read().decode('utf-8', 'replace'))
                return True, 'ok'

            @staticmethod
            def send(text, **kw):
                return True, 'ok'

        R._send_files('2026-09-01', FakeTG, only_fresh=False)
        self.assertTrue(any('코스맥스' in s for s in sent_paths),
                        '보관본이 아니라 빈 셸을 보냈다')

    # ── 중복 발송 방지 (--once) ────────────────────────
    def test_once_skips_a_board_already_sent(self):
        """크론이 여러 번 깨어나도 같은 보드가 두 번 오면 안 된다."""
        self._site('2026-09-01')
        R._send_files('2026-09-01', self._fake_tg(), only_fresh=False, once=True)
        n = len(self.sent)
        self.assertTrue(n)
        R._send_files('2026-09-01', self._fake_tg(), only_fresh=False, once=True)
        self.assertEqual(len(self.sent), n, '이미 보낸 보드를 다시 보냈다')

    def test_once_still_sends_a_new_day(self):
        self._site('2026-09-01')
        R._send_files('2026-09-01', self._fake_tg(), only_fresh=False, once=True)
        self._site('2026-09-02')
        n = len(self.sent)
        R._send_files('2026-09-02', self._fake_tg(), only_fresh=False, once=True)
        self.assertGreater(len(self.sent), n, '다음 날 보드는 보내야 한다')

    def test_failed_send_is_retried(self):
        """실패한 날을 '보냈다' 고 적으면 재시도가 통째로 막힌다."""
        self._site('2026-09-01')

        class DeadTG:
            @staticmethod
            def send_document(path, caption='', **kw):
                return False, '네트워크 실패'

            @staticmethod
            def send(text, **kw):
                return False, '네트워크 실패'

        rc = R._send_files('2026-09-01', DeadTG, only_fresh=False, once=True)
        self.assertEqual(rc, 1)
        self.assertIsNone(R._sent_mark(), '실패했는데 표식을 남겼다')
        rc = R._send_files('2026-09-01', self._fake_tg(), only_fresh=False, once=True)
        self.assertEqual(rc, 0)
        self.assertTrue(self.sent, '실패한 날은 다시 보내야 한다')

    def test_once_without_mark_sends(self):
        self._site('2026-09-01')
        self.assertIsNone(R._sent_mark())
        R._send_files('2026-09-01', self._fake_tg(), only_fresh=False, once=True)
        self.assertTrue(self.sent)


if __name__ == '__main__':
    unittest.main()
