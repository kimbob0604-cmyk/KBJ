"""
텔레그램 인박스 검증 — board/ingest/tg_inbox.py (KBJ P2 뒤 legacy 에 남은 부분). 네트워크 없음.

KBJ P2(설계 §5.8·§5.10 #33): getUpdates·offset 파일은 없어졌고, 갱신은 KBJ notifier 웹훅이 받아
저장소(`prv_alerts.tg_inbox`)에 쌓는다. 파싱·링크·oEmbed·화이트리스트 시험은 정본과 함께
kbj `tests/unit/notifier/test_inbox.py` 로 옮겼다. 여기 남은 것은 **legacy 코드가 아직 하는 일**이다:

1. 저장소에서 읽은 항목을 `state/inbox.json` 계약 그대로 합친다(`drain`·`merge`) — update_id 중복
   제거, 보존 기간이 지난 항목 버리기.
2. 손상된 inbox.json 을 빈 기본값으로 덮어쓰지 않는다(`read_inbox` — `.bad` 로 옮기고 로그).
3. `run.cmd_inbox` 의 종료 코드 — 설정이 없으면 건너뜀(0), 수집 실패만 1.
4. 읽을 대화방 — 목록이 하나짜리보다 우선(`inbox_chat_ids`).

원래 시험(옛 `Base.drain` = 가짜 getUpdates)의 단언은 그대로 두고, 갱신을 주는 자리만 저장소 대역
(`FakeStore`)으로 바꿨다(MIGRATION.md 'P2').
"""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from pydantic import SecretStr

from ..ingest import tg_inbox as TI
from ..ingest.http import Fetch

NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=TI.KST)
CHAT = 777


def item(uid, text, chat=CHAT, when=NOW, kind='other'):
    """notifier 저장소가 돌려주는 항목(계약 D-086 모양)."""
    return dict(update_id=uid, chat_id=chat, date=when.isoformat(timespec='seconds'),
                text=text, urls=[], x_ids=[], author=None, kind=kind, text_via=None)


class FakeStore:
    """KBJ notifier 인박스 저장소 대역 — `read_items` 가 부르는 `items(since)` 만.

    정본 저장소처럼 항목 date 가 since 보다 이르면 빼고 돌려준다. `honor_since=False` 는 기간 밖
    항목까지 돌려주는 저장소 — legacy `merge` 가 스스로 보존 기간을 지키는지 본다."""

    def __init__(self, items=(), received=NOW, honor_since=True):
        self.rows = [(dict(it), received) for it in items]
        self.honor_since = honor_since
        self.since = []

    def items(self, since=None):
        self.since.append(since)
        return [(dict(it), rec) for it, rec in self.rows
                if not self.honor_since or since is None
                or datetime.fromisoformat(it['date']) >= since]


def fake_settings(**kw):
    """`Settings()` 자리 — 준 값만 SecretStr 로 채운 설정(실제 .env·환경변수를 읽지 않는다)."""
    vals = dict(database_url=None, telegram_inbox_chat_ids=None, telegram_chat_id=None)
    vals.update({k: SecretStr(v) for k, v in kw.items()})
    ns = SimpleNamespace(**vals)
    return lambda *a, **k: ns


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tginbox-')
        self.inbox = os.path.join(self.tmp, 'inbox.json')

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def drain(self, store, chat_ids=(CHAT,), now=NOW, **kw):
        return TI.drain(list(chat_ids), self.inbox, store=store,
                        log=lambda *a: None, now=now, **kw)

    def read(self):
        with open(self.inbox, encoding='utf-8') as f:
            return json.load(f)


# ── (a) 저장소 → inbox.json ──────────────────────────────────
class Drain(Base):
    def test_items_from_the_store_are_written_in_the_contract_shape(self):
        st = FakeStore([item(1, '첫'), item(2, 'https://x.com/a/status/1', kind='x')])
        r = self.drain(st)
        self.assertEqual((r['n_new'], r['n_x'], r['n_kept']), (2, 1, 2))
        d = self.read()
        self.assertEqual(d['source'], 'telegram')
        self.assertEqual(d['chat_ids'], [CHAT])
        self.assertEqual(d['updated_at'], NOW.isoformat(timespec='seconds'))
        self.assertEqual([x['update_id'] for x in d['items']], [1, 2])
        # 저장소에는 보존 기간 시작 시각부터만 묻는다
        self.assertEqual(st.since, [NOW - timedelta(days=TI.KEEP_DAYS)])

    def test_no_chat_ids_is_an_error(self):
        with self.assertRaises(Fetch):
            self.drain(FakeStore(), chat_ids=[])
        self.assertFalse(os.path.exists(self.inbox), '실패한 수집이 빈 파일을 남기면 안 된다')


# ── (b) 중복·보존 기간 ────────────────────────────────────────
class DedupExpiry(Base):
    def test_same_update_id_is_not_added_twice(self):
        self.drain(FakeStore([item(1, '한 번')]))
        # 보존 기간 안의 항목은 다음 실행에서도 저장소가 또 돌려준다.
        r = self.drain(FakeStore([item(1, '한 번'), item(2, '새로')]))
        self.assertEqual(r['n_new'], 1)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [1, 2])

    def test_items_older_than_keep_days_are_dropped(self):
        old = NOW - timedelta(days=15)
        edge = NOW - timedelta(days=13)
        r = self.drain(FakeStore([item(1, '옛날', when=old), item(2, '아직', when=edge)],
                                 honor_since=False))
        self.assertEqual(r['n_expired'], 1)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [2])

    def test_expiry_applies_to_stored_items_on_a_later_run(self):
        self.drain(FakeStore([item(1, '그때는 신선')]))
        r = self.drain(FakeStore([]), now=NOW + timedelta(days=15))
        self.assertEqual(r['n_expired'], 1)
        self.assertEqual(self.read()['items'], [])

    def test_keep_days_is_configurable(self):
        st = FakeStore([item(1, 'x', when=NOW - timedelta(days=3))], honor_since=False)
        r = self.drain(st, keep_days=2)
        self.assertEqual(r['n_expired'], 1)
        self.assertEqual(st.since, [NOW - timedelta(days=2)])

    def test_corrupt_inbox_is_moved_aside_and_logged_not_overwritten(self):
        """반쪽 쓰기·잘못 고친 파일을 빈 기본값으로 덮어쓰면 14일치가 조용히 사라진다.
        `.bad` 로 옮기고 사유를 남긴 뒤 진행한다."""
        self.drain(FakeStore([item(1, '살아 있던 것'), item(2, '이것도')]))
        with open(self.inbox, 'w', encoding='utf-8') as f:
            f.write('{"items":[{"update_id":1,')
        logs = []
        r = TI.drain([CHAT], self.inbox, store=FakeStore([item(3, '새')]),
                     log=logs.append, now=NOW)
        self.assertEqual(r['n_new'], 1)
        self.assertEqual([x['update_id'] for x in self.read()['items']], [3])
        with open(self.inbox + '.bad', encoding='utf-8') as f:
            self.assertEqual(f.read(), '{"items":[{"update_id":1,', '옛 파일은 그대로 남는다')
        self.assertTrue(any('손상' in m and '.bad' in m for m in logs), logs)


# ── (c) run.cmd_inbox 종료 코드 ───────────────────────────────
class CmdInboxExit(unittest.TestCase):
    def test_missing_config_is_a_skip_not_a_failure(self):
        """주말 크론은 --inbox 만 돈다. 설정이 없는 환경에서 1 을 돌려주면 set -e 아래
        잡이 매주 두 번 빨갛게 되어 진짜 실패가 묻힌다 (GITHUB.md 2장)."""
        from .. import run
        with mock.patch('kbj.config.settings.Settings', fake_settings()), \
                mock.patch.object(TI, 'Settings', fake_settings()):
            self.assertEqual(run.cmd_inbox(), 0)
        db_only = fake_settings(database_url='postgresql://synthetic.invalid/kbj')
        with mock.patch('kbj.config.settings.Settings', db_only), \
                mock.patch.object(TI, 'Settings', db_only):
            self.assertEqual(run.cmd_inbox(), 0, '대화방이 없어도 건너뜀이지 실패가 아니다')

    def test_fetch_failure_is_reported_as_failure(self):
        from .. import run
        st = fake_settings(database_url='postgresql://synthetic.invalid/kbj',
                           telegram_chat_id=str(CHAT))

        def boom(*a, **k):
            raise Fetch('인박스 저장소 읽기 거부')

        with mock.patch('kbj.config.settings.Settings', st), \
                mock.patch.object(TI, 'Settings', st), mock.patch.object(TI, 'drain', boom):
            self.assertEqual(run.cmd_inbox(), 1)


# ── (d) 읽을 대화방 ───────────────────────────────────────────
class ChatIdsFromCreds(unittest.TestCase):
    def test_list_beats_single_and_single_is_the_default(self):
        with mock.patch.object(TI, 'Settings', fake_settings(telegram_inbox_chat_ids='1, 2',
                                                             telegram_chat_id='9')):
            self.assertEqual(TI.inbox_chat_ids(), [1, 2])
        with mock.patch.object(TI, 'Settings', fake_settings(telegram_chat_id='9')):
            self.assertEqual(TI.inbox_chat_ids(), [9])
        with mock.patch.object(TI, 'Settings', fake_settings()):
            self.assertEqual(TI.inbox_chat_ids(), [])


if __name__ == '__main__':
    unittest.main()
