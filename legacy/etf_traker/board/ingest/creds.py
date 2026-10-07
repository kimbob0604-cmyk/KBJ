#!/usr/bin/env python3
"""
자격증명 로딩 — board/.env 또는 환경변수.

.env 는 .gitignore 에 걸려 있고 커밋되지 않는다. GitHub Actions 에서는 .env 대신
레포 Secrets 를 환경변수로 주입한다. 두 경로 모두 여기로 들어온다.

의존성을 늘리지 않으려고 python-dotenv 를 쓰지 않고 직접 읽는다.
값은 절대 로그에 찍지 않는다 — mask() 를 거친 것만 출력한다.
"""
import os

from ..engine.config import ROOT

ENV_PATH = os.path.join(ROOT, '.env')
_loaded = False

# 이름 → 사람이 읽는 설명. --check 와 문서가 함께 쓴다.
KEYS = {
    'KIS_APP_KEY': '한국투자증권 앱키',
    'KIS_APP_SECRET': '한국투자증권 시크릿',
    'KRX_API_KEY': 'KRX 오픈API 인증키',
    'DART_API_KEY': 'DART OpenAPI 인증키',
    'NAVER_CLIENT_ID': '네이버 검색 API 클라이언트 ID',
    'NAVER_CLIENT_SECRET': '네이버 검색 API 시크릿',
    'DATAGO_KEY': '공공데이터포털 인증키',
    'ANTHROPIC_API_KEY': 'Anthropic API 키',
    'TELEGRAM_BOT_TOKEN': '텔레그램 봇 토큰',
    'TELEGRAM_CHAT_ID': '텔레그램 대화방 ID',
    # 선택. 인박스(ingest/tg_inbox.py)가 읽을 대화방 — 쉼표 구분. 없으면
    # TELEGRAM_CHAT_ID 하나(사용자와 봇의 1:1 대화)를 읽는다.
    'TRIGGER_INBOX_CHAT_IDS': '인박스 대화방 ID 목록 (선택)',
}


def load(path=ENV_PATH, override=False):
    """.env 를 os.environ 에 올린다. 이미 있는 환경변수는 기본적으로 덮지 않는다."""
    global _loaded
    if _loaded and not override:
        return
    _loaded = True
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not v:
                continue
            if override or not os.environ.get(k):
                os.environ[k] = v


def get(name, required=False):
    load()
    v = os.environ.get(name, '').strip()
    if not v and required:
        raise RuntimeError(
            f'{name} 이 없다 ({KEYS.get(name, "")}). '
            f'board/.env 에 넣거나 환경변수로 주입하라. 예시는 board/.env.example.')
    return v


def has(*names):
    load()
    return all(os.environ.get(n, '').strip() for n in names)


def mask(v):
    """로그용. 앞 4자리만 남긴다. 값 자체를 절대 찍지 않는다."""
    if not v:
        return '(없음)'
    return v[:4] + '…' + f'({len(v)}자)'


def status():
    """--check 용. [(이름, 설명, 있는지, 마스킹된 값)]"""
    load()
    return [(k, d, bool(os.environ.get(k, '').strip()),
             mask(os.environ.get(k, ''))) for k, d in KEYS.items()]
