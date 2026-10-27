"""작업 오류 목록(kbj/services/ops/errors.py · `python -m kbj.services.ops errors`) — 합성 기록만.

마지막 시도 기준 셈(재시도 끝 성공은 실패 아님), 계획됨 제외, 기다림(detail.wait) 따로, 사유 가림,
정렬, CLI 글·JSON·구간·오류 종료 코드."""

from __future__ import annotations

import io
import json
from dataclasses import replace
from datetime import date, time, timedelta

from kbj.core.calendar import TradingCalendar
from kbj.services.ops.__main__ import Deps, main
from kbj.services.ops.errors import job_errors, render_errors
from kbj.services.ops.source import MemoryOpsSource
from tests.unit.ops.ops_world import EXPECT, fresh_ages, kst, run_row, week_source

CAL = TradingCalendar.default()
D = date(2026, 10, 16)
NOW = kst(D, time(21, 0))
SECRET = "crtfc_key=0123456789abcdef0123456789abcdef01234567"


def _src() -> MemoryOpsSource:
    src = week_source()
    # board.daily: 1회 실패 뒤 재시도 성공 → 실패 아님(재시도 1)
    src.runs = [r for r in src.runs if not (r.job == "board.daily" and r.as_of == D.isoformat())]
    src.runs.append(
        replace(run_row("board.daily", D, time(15, 45), "failed", 1), reason="HTTP 500")
    )
    src.runs.append(run_row("board.daily", D, time(15, 45), "ok", 2))
    # krx.daily: 이틀 최종 실패(사유에 키 모양 — 가린다)
    for d in (D, D - timedelta(days=1)):
        src.runs.append(replace(run_row("krx.daily", d, time(9, 55), "failed", 12),
                                reason=f"KrxError 401 {SECRET}"))  # fmt: skip
    # macro.monthly: 공표 대기(skipped·wait) 하나, trade.derive: 의존 막힘 하나
    src.runs.append(replace(run_row("macro.monthly", D, time(10, 10), "skipped", 2),
                            reason="공표 전: ECOS:161Y005@2026-09", wait="not_ready"))  # fmt: skip
    src.runs.append(replace(run_row("trade.derive", D, time(10, 40), "skipped"),
                            reason="굳은 의존 trade.customs_monthly@2026-09 가 기다림",
                            wait="blocked"))  # fmt: skip
    # 계획됨(꺼진 작업)과 진행 중은 오류가 아니다
    src.runs.append(replace(run_row("krx.vkospi", D, time(8, 10), "skipped"), reason="planned"))
    src.runs.append(run_row("market.close_collect", D, time(20, 50), "running"))
    # 시간 초과
    src.runs.append(run_row("flows.market_minute", D, time(9, 1), "timeout"))
    return src


def test_job_errors_counts_final_attempts_and_skips_planned() -> None:
    got = {e.job: e for e in job_errors(_src().runs)}
    assert set(got) == {"krx.daily", "macro.monthly", "trade.derive", "flows.market_minute"}
    k = got["krx.daily"]
    assert (k.runs, k.failed, k.timeout, k.skipped, k.retries) == (2, 2, 0, 0, 0)
    assert k.last_as_of == D.isoformat() and k.last_status == "failed"
    assert k.last_reason is not None and SECRET.split("=")[1] not in k.last_reason
    m = got["macro.monthly"]
    assert (m.skipped, m.waiting, m.failed) == (1, 1, 0)
    assert m.last_status == "skipped(not_ready)"
    assert got["trade.derive"].last_status == "skipped(blocked)"
    assert got["flows.market_minute"].timeout == 1


def test_retry_then_success_is_not_listed() -> None:
    src = week_source()
    src.runs.append(replace(run_row("board.daily", D, time(15, 45), "failed", 1), reason="x"))
    src.runs.append(run_row("board.daily", D, time(15, 45), "ok", 2))
    assert job_errors(src.runs) == []
    assert "없음" in render_errors([], NOW - timedelta(days=7), NOW)


def test_order_failures_first_then_skips() -> None:
    order = [e.job for e in job_errors(_src().runs)]
    assert order == ["krx.daily", "flows.market_minute", "macro.monthly", "trade.derive"]


def _deps(src: MemoryOpsSource) -> Deps:
    return Deps(src, EXPECT, CAL, fresh_ages(NOW), lambda: (50, 100))


def test_cli_errors_text_and_json() -> None:
    out = io.StringIO()
    code = main(["errors"], deps=lambda: _deps(_src()), now=lambda: NOW, out=out)
    text = out.getvalue()
    assert code == 0 and "문제 있는 작업 4" in text and "krx.daily" in text
    assert SECRET.split("=")[1] not in text
    out = io.StringIO()
    code = main(["errors", "--since", "24h", "--json"], deps=lambda: _deps(_src()),
                now=lambda: NOW, out=out)  # fmt: skip
    body = json.loads(out.getvalue())
    jobs = {j["job"]: j for j in body["jobs"]}
    assert code == 0 and jobs["krx.daily"]["failed"] == 1  # 24시간 — 전날 실패는 구간 밖
    assert jobs["macro.monthly"]["waiting"] == 1
    assert body["since"] == (NOW - timedelta(hours=24)).isoformat()


def test_cli_errors_store_error_is_exit_2() -> None:
    from kbj.store.db import StoreError

    def boom() -> Deps:
        raise StoreError("운영 기록 조회 실패: OperationalError postgresql://u:pw@h/db")

    err = io.StringIO()
    assert main(["errors"], deps=boom, now=lambda: NOW, err=err) == 2
    assert "pw" not in err.getvalue()
