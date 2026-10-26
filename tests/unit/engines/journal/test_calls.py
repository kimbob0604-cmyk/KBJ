"""공개 콜 기록 엔진 — 읽기·칸 모순·고친 흔적·성과(ADR 0042, metrics §32). 값은 전부 합성."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal

import pytest

from kbj.core.quality import Quality
from kbj.engines.journal.calls import (
    CallRecord,
    call_mark,
    check_call,
    check_new_call,
    duplicate_ids,
    immutable_changes,
    parse_call,
)

PATH = "calls/2026/2026-10-27-000001-alpha-thesis.md"
TODAY = date(2026, 10, 27)

HEAD = {
    "id": "2026-10-27-000001-alpha-thesis",
    "opened": "2026-10-27",
    "market": "KR",
    "code": '"000001"',
    "name": "합성종목",
    "direction": "long",
    "horizon_months": "12",
    "sector": "반도체",
    "entry_mcap_eok": "10000",
    "entry_mcap_as_of": "2026-10-27",
    "entry_mcap_source": "합성",
    "target_mcap_eok": "13000",
    "benchmark": "KOSPI",
    "status": "open",
}
BODY = "## 논리\n\n- 합성 논리\n\n## 밸류에이션\n\n- 합성\n\n## 갱신\n"


def _text(
    body: str = BODY,
    kill: tuple[str, ...] = ("킬 조건 하나",),
    over: Mapping[str, str | None] | None = None,
    **kw: str | None,
) -> str:
    head = {**HEAD, **(over or {}), **kw}
    lines = ["---"]
    lines += [f"{k}: {v}" for k, v in head.items() if v is not None]
    if kill:
        lines.append("kill_criteria:")
        lines += [f"  - {k}" for k in kill]
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _rec(text: str | None = None, path: str = PATH) -> CallRecord:
    r = parse_call(path, text if text is not None else _text())
    assert isinstance(r, CallRecord), r
    return r


def _fields(res: object) -> set[str | None]:
    assert isinstance(res, list)
    return {p.field for p in res}


# ── 읽기 ──


def test_parse_ok() -> None:
    r = _rec()
    assert r.code == "000001"
    assert r.entry_mcap_eok == Decimal(10000)
    assert r.implied_move == Decimal("0.3")
    assert r.kill_criteria == ("킬 조건 하나",)
    assert r.currency == "KRW"
    assert r.locked_body == "## 논리\n- 합성 논리\n## 밸류에이션\n- 합성"


def test_unquoted_leading_zero_code_is_rejected() -> None:
    # YAML 이 000001 을 정수 1 로 읽는다 → 6자리 형식 위반으로 잡혀야 한다(앞자리 0 보존)
    assert "code" in _fields(parse_call(PATH, _text(code="000001")))


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"market": "JP"}, "market"),
        ({"direction": "up"}, "direction"),
        ({"status": "done"}, "status"),
        ({"benchmark": "HSI"}, "benchmark"),
        ({"horizon_months": "0"}, "horizon_months"),
        ({"entry_mcap_eok": "-5"}, "entry_mcap_eok"),
        ({"target_mcap_eok": "abc"}, "target_mcap_eok"),
        ({"opened": "2026/10/27"}, "opened"),
        ({"id": "다른-이름"}, "id"),
        ({"name": None}, "name"),
        ({"typo_field": "x"}, "typo_field"),
    ],
)
def test_parse_rejects(over: dict[str, str | None], field: str) -> None:
    assert field in _fields(parse_call(PATH, _text(over=over)))


def test_parse_requires_kill_criteria_and_logic() -> None:
    assert "kill_criteria" in _fields(parse_call(PATH, _text(kill=())))
    assert None in _fields(parse_call(PATH, _text(body="## 밸류에이션\n- x\n")))


def test_parse_file_name_rules() -> None:
    assert None in _fields(parse_call("calls/2026-10-27-000001-alpha.md", _text()))
    assert None in _fields(parse_call("calls/2026/2026-10-27-000001-Alpha.md", _text()))
    other_day = "calls/2026/2026-10-28-000001-alpha-thesis.md"
    assert "opened" in _fields(parse_call(other_day, _text(id="2026-10-28-000001-alpha-thesis")))


def test_parse_without_front_matter_or_bad_yaml() -> None:
    assert None in _fields(parse_call(PATH, "## 논리\n"))
    assert None in _fields(parse_call(PATH, "---\nid: [\n---\n## 논리\n"))


# ── 칸 모순 ──


def test_check_direction_vs_target() -> None:
    assert check_call(_rec(), TODAY) == []
    short_bad = _rec(_text(direction="short"))
    assert {p.field for p in check_call(short_bad, TODAY)} == {"target_mcap_eok"}
    long_bad = _rec(_text(target_mcap_eok="9000"))
    assert {p.field for p in check_call(long_bad, TODAY)} == {"target_mcap_eok"}


def test_check_dates() -> None:
    assert {p.field for p in check_call(_rec(), date(2026, 10, 26))} == {"opened"}
    late = _rec(_text(entry_mcap_as_of="2026-10-28"))
    assert "entry_mcap_as_of" in {p.field for p in check_call(late, TODAY)}
    old = _rec(_text(entry_mcap_as_of="2026-10-01"))
    assert "entry_mcap_as_of" in {p.field for p in check_call(old, TODAY)}


def test_check_close_fields() -> None:
    half = _rec(_text(status="closed", closed="2026-11-02"))
    assert {p.field for p in check_call(half, date(2026, 11, 3))} == {
        "close_mcap_eok",
        "close_reason",
    }
    ok = _rec(
        _text(
            status="closed", closed="2026-11-02", close_mcap_eok="12000", close_reason="목표 근접"
        )
    )
    assert check_call(ok, date(2026, 11, 3)) == []
    open_with_close = _rec(_text(close_reason="미리 씀"))
    assert {p.field for p in check_call(open_with_close, TODAY)} == {"close_reason"}


# ── 고친 흔적 ──


def test_immutable_fields_and_locked_body() -> None:
    old = _rec()
    assert immutable_changes(old, _rec()) == []
    moved = _rec(_text(target_mcap_eok="15000"))
    assert {p.field for p in immutable_changes(old, moved)} == {"target_mcap_eok"}
    rewritten = _rec(_text(body=BODY.replace("합성 논리", "고친 논리")))
    assert {p.field for p in immutable_changes(old, rewritten)} == {None}
    # 갱신 아래 덧붙임·이름 고치기·촉매 추가는 된다
    appended = _rec(_text(body=BODY + "- 2026-11-01: 갱신 한 줄\n", name="합성종목(우)"))
    assert immutable_changes(old, appended) == []
    # 줄 끝 공백·빈 줄 차이는 무시
    spaced = _rec(_text(body=BODY.replace("- 합성 논리\n", "- 합성 논리   \n\n\n")))
    assert immutable_changes(old, spaced) == []


def test_closed_call_cannot_reopen_or_change_close() -> None:
    closed = _rec(
        _text(status="closed", closed="2026-11-02", close_mcap_eok="12000", close_reason="r")
    )
    assert {p.field for p in immutable_changes(closed, _rec())} >= {"status"}
    changed = _rec(
        _text(status="closed", closed="2026-11-02", close_mcap_eok="12500", close_reason="r")
    )
    assert {p.field for p in immutable_changes(closed, changed)} == {"close_mcap_eok"}
    # open → closed 는 된다
    assert immutable_changes(_rec(), closed) == []


def test_backdating_and_duplicates() -> None:
    r = _rec()
    assert check_new_call(r, date(2026, 10, 30)) == []
    assert [p.field for p in check_new_call(r, date(2026, 10, 31))] == ["opened"]
    assert [p.field for p in duplicate_ids([r, r])] == ["id"]


# ── 성과 ──


def test_call_mark_long_by_hand() -> None:
    r = _rec()
    m = call_mark(r, Decimal(11500), bench_entry=Decimal(2500), bench_mark=Decimal(2600))
    assert m.call_return == Decimal("0.15")
    assert m.benchmark_return == Decimal("0.04")
    assert m.relative == Decimal("0.11")
    assert m.target_progress == Decimal("0.5")
    assert m.target_hit is False
    assert m.quality is Quality.OK


def test_call_mark_short_by_hand() -> None:
    r = _rec(_text(direction="short", target_mcap_eok="7000"))
    m = call_mark(
        r,
        Decimal(8000),
        bench_entry=Decimal(100),
        bench_mark=Decimal(95),
        bench_quality=Quality.STALE,
    )
    assert m.stock_return == Decimal("-0.2")
    assert m.call_return == Decimal("0.2")
    assert m.relative == Decimal("0.15")  # 벤치 −5% − 종목 −20%
    assert m.target_progress == Decimal("2") / Decimal("3")
    assert m.quality is Quality.STALE
    hit = call_mark(r, Decimal(6900))
    assert hit.target_hit is True
    assert hit.benchmark_return is None
    assert hit.relative is None


def test_call_mark_unknown_or_invalid_is_none() -> None:
    r = _rec()
    for m in (call_mark(r, None), call_mark(r, Decimal(11000), mark_quality=Quality.INVALID)):
        assert m.call_return is None
        assert m.target_hit is None
        assert m.quality is Quality.INVALID
