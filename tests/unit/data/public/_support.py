"""공개 어댑터 시험 공용 — 기록하는 리미터·가짜 서버·합성 fixture 읽기(묶음 C).

키는 모두 가짜 값이다. 응답 모양은 공식 문서(probe_results §1~§5)대로 만든 합성 문자열이다.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import SecretStr

from kbj.data.budget import DailyBudget
from kbj.data.datago import DatagoTransport
from kbj.data.ratelimit import Priority
from tests.fakes.clock import FakeClock

ROOT = Path(__file__).resolve().parents[4]
SYNTH = ROOT / "tests" / "fixtures" / "synthetic" / "datago"
T0 = datetime(2026, 10, 7, 1, 0, tzinfo=UTC)  # 10:00 KST

Reply = Callable[[httpx.Request], tuple[int, bytes | str]]


class RecordingLimiter:
    """`RateLimiter` 모양 — 허가 요청과 감속 신호를 센다."""

    def __init__(self) -> None:
        self.acquired: list[tuple[Priority, str]] = []
        self.slowdowns = 0

    def acquire(self, priority: Priority, tr_id: str, timeout: float | None = None) -> None:
        self.acquired.append((priority, tr_id))

    def on_rate_limited(self) -> float:
        self.slowdowns += 1
        return 1.0


class FakeServer:
    """`httpx.MockTransport` 처리기 — 요청을 남기고 `reply(요청)` 의 (상태, 본문)을 준다."""

    def __init__(self, reply: Reply) -> None:
        self.reply = reply
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.reply(request)
        return httpx.Response(status, content=body.encode() if isinstance(body, str) else body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def params(self, i: int = -1) -> dict[str, str]:
        q = parse_qs(urlsplit(str(self.requests[i].url)).query, keep_blank_values=True)
        return {k: v[0] for k, v in q.items()}

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


def synth(name: str) -> str:
    """합성 fixture 내용(UTF-8)."""
    return (SYNTH / name).read_text(encoding="utf-8")


def datago_transport(
    server: FakeServer,
    dataset_ids: Iterable[str],
    *,
    key: str = "fake-datago-key",
    cap: int = 9500,
    clock: FakeClock | None = None,
) -> tuple[DatagoTransport, dict[str, RecordingLimiter], dict[str, DailyBudget]]:
    """데이터셋마다 기록 리미터·프로세스 안 예산을 붙인 전송층(재시도 대기는 0)."""
    clk = clock or FakeClock(T0)
    ids = list(dataset_ids)
    limiters = {d: RecordingLimiter() for d in ids}
    budgets = {d: DailyBudget(None, "datago", cap, scope=d) for d in ids}
    t = DatagoTransport(
        SecretStr(key),
        limiters=limiters,
        budgets=budgets,
        transport=server.transport(),
        retry_waits_s=(0.0,),
        now=clk,
        sleep=lambda _: None,
        monotonic=clk.monotonic,
    )
    return t, limiters, budgets


def corp_zip(rows: Iterable[tuple[str, str, str, str]], *, name: str = "CORPCODE.xml") -> bytes:
    """합성 corpCode.xml zip — 행 `(corp_code, corp_name, stock_code, modify_date)`.

    DART 원본 모양(`<result><list>…</list></result>`, 비상장 stock_code 는 공백 한 칸).
    """
    body = "".join(
        f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name>"
        f"<corp_eng_name>SYNTHETIC</corp_eng_name><stock_code>{s or ' '}</stock_code>"
        f"<modify_date>{d}</modify_date></list>"
        for c, n, s, d in rows
    )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><result>{body}</result>'.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, xml)
    return buf.getvalue()
