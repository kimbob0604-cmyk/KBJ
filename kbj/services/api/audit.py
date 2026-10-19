"""화면 데이터 점검 — 로그인 화면이 부르는 GET API 를 앱 안에서 한 번씩 불러
상태·기준 시각·품질·빈 값을 한 줄씩 찍는다(`python -m kbj.services.api audit`,
로컬: `bash scripts/local/kbj.sh audit`).

- 비밀번호 없이 돈다: 세션 검사를 이 실행 안에서만 통과시킨다(서버·쿠키를 건드리지 않는다).
- 기본 출력은 경로·상태·나이·품질·빈 칸 수뿐 — 값은 `--show 경로` 로 한 응답만 본다(로컬 화면에만).
- 경로 인자 중 `{code}` 는 `--code`(기본 005930)로 채우고, 그 밖의 인자가 든 경로는 건너뛴다.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbj.core.time import KST
from kbj.services.api.auth import SessionData
from kbj.services.api.deps import require_session

SKIP_PREFIXES: Final = ("/api/auth", "/api/webhook")
DEFAULT_CODE: Final = "005930"


@dataclass(frozen=True, slots=True)
class AuditRow:
    path: str
    status: int
    as_of: str | None = None
    age_h: float | None = None
    quality: str | None = None
    source: str | None = None
    empty: int = 0  # data 안의 빈 목록·null 수
    message: str = ""  # 404·오류 본문의 message(작업 이름·예정 시각)


def get_paths(app: FastAPI, code: str) -> list[str]:
    """점검할 GET 경로 — `{code}` 만 채우고 다른 인자가 든 경로·인증·웹훅은 뺀다."""
    out: list[str] = []
    for p, ops in app.openapi().get("paths", {}).items():
        if "get" not in ops or not p.startswith("/api/") or p.startswith(SKIP_PREFIXES):
            continue
        p = p.replace("{code}", code)
        if "{" not in p and p not in out:
            out.append(p)
    return sorted(out)


def count_empty(v: Any) -> int:
    """빈 목록·null 의 수(중첩 포함)."""
    if v is None or v == []:
        return 1
    if isinstance(v, dict):
        return sum(count_empty(x) for x in v.values())  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]
    if isinstance(v, list):
        return sum(count_empty(x) for x in v)  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]
    return 0


def _row(path: str, status: int, body: Any, now: datetime) -> AuditRow:
    if not isinstance(body, dict):
        return AuditRow(path, status)
    if status == 422:
        return AuditRow(path, status, message="필수 인자가 있는 경로 — 건너뜀")
    if status != 200:
        return AuditRow(path, status, message=str(body.get("message", ""))[:160])  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
    as_of = body.get("as_of")  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
    age = None
    if isinstance(as_of, str):
        age = round((now - datetime.fromisoformat(as_of)).total_seconds() / 3600, 1)
    return AuditRow(
        path,
        status,
        as_of=as_of if isinstance(as_of, str) else None,
        age_h=age,
        quality=body.get("quality"),  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        source=body.get("source"),  # pyright: ignore[reportUnknownMemberType,reportUnknownArgumentType]
        empty=count_empty(body.get("data")) if "data" in body else 0,  # pyright: ignore[reportUnknownMemberType]
    )


def _session() -> SessionData:
    t = datetime.now(KST).isoformat()
    return SessionData(user="audit", csrf="audit", created=t, last_seen=t)


def audit_client(app: FastAPI) -> TestClient:
    """세션 검사를 통과시킨 클라이언트(이 앱 객체에만)."""
    app.dependency_overrides[require_session] = _session
    return TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False)


def run_audit(
    app: FastAPI, now: Callable[[], datetime], code: str = DEFAULT_CODE
) -> list[AuditRow]:
    c = audit_client(app)
    rows: list[AuditRow] = []
    for p in get_paths(app, code):
        r = c.get(p)
        try:
            body = r.json()
        except ValueError:
            body = None
        rows.append(_row(p, r.status_code, body, now()))
    return rows


def render(items: Iterable[AuditRow]) -> str:
    rows = list(items)
    lines = [
        f"{'상태':>4}  {'나이(h)':>8}  {'품질':<10} {'빈칸':>4}  경로  (기준 시각 · 원천 · 사유)"
    ]
    for r in rows:
        age = "" if r.age_h is None else f"{r.age_h:.1f}"
        tail = r.message or " · ".join(x for x in (r.as_of, r.source) if x)
        lines.append(
            f"{r.status:>4}  {age:>8}  {r.quality or '':<10} {r.empty:>4}  {r.path}  ({tail})"
        )
    bad = [r for r in rows if r.status >= 500]
    nod = [r for r in rows if r.status == 404]
    lines.append(f"— {len(rows)}개 · 오류(5xx) {len(bad)} · 데이터 없음(404) {len(nod)}")
    return "\n".join(lines)


def show(app: FastAPI, path: str) -> str:
    """한 응답 전체(JSON) — 값 대조용. 로컬 화면에만 찍는다."""
    r = audit_client(app).get(path)
    try:
        return json.dumps(r.json(), ensure_ascii=False, indent=2)
    except ValueError:
        return f"{r.status_code} {r.text[:500]}"
