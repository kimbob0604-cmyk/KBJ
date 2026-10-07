"""auth 시험 공통 — 이 폴더의 시험은 auth 프로세스로 돈다(`KBJ_SERVICE=auth`).

발급자(`kbj/services/auth/issuer.py`)는 런타임 가드로 auth 프로세스에서만 만들어진다(ADR 0004).
GEXLAB 에서 옮긴 시험은 발급자를 설정 없이 바로 만들므로(`KisTokenIssuer(http, key, secret,
now=…)`), 프로세스 설정(`Settings()`)이 auth 가 되게 환경변수를 둔다. 가드 자체는
`test_auth_guard.py` 가 본다 (그 시험은 이 값을 지우거나 바꾼다).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auth_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBJ_SERVICE", "auth")
