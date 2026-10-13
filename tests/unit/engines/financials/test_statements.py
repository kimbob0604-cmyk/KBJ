"""재무제표 누적→분기(dart-report `statements` 승격본) — docs/p4_design.md §4.5·§8.3.

골든(G1·G2)이 legacy 와 같은지 지키고, 여기서는 규칙 하나씩: 누적 차분·4Q = 연간 − 3분기
누적·상태표는 차분하지 않음·직전 분기 없음(derived)·CFS→OFS 폴백·1H/9M/3M 라벨·legacy `_pick` 의 0
처리(D-P4-8).
"""

from __future__ import annotations

from typing import Any

from kbj.engines.financials.statements import (
    EOK,
    Period,
    _pick,
    fetch_periods,
    to_annual,
    to_quarterly,
)


def _p(year: int, q: str, rev: float, bal: float = 100.0, fs: str = "CFS") -> Period:
    return Period(year, q, fs, {"revenue": rev * EOK, "operating_income": rev * 0.1 * EOK},
                  {"total_assets": bal * EOK})  # fmt: skip


def test_누적_차분과_4분기() -> None:
    rows = to_quarterly([_p(2025, "1Q", 100), _p(2025, "2Q", 250), _p(2025, "3Q", 330),
                         _p(2025, "4Q", 500, bal=140)])  # fmt: skip
    assert [r["revenue"] for r in rows] == [100.0, 150.0, 80.0, 170.0]
    assert [r["label"] for r in rows] == ["1Q25", "2Q25", "3Q25", "4Q25"]
    assert rows[3]["total_assets"] == 140.0  # 상태표는 시점값 그대로
    assert rows[1]["opm"] == 15.0 / 150.0
    assert not any(r["derived"] for r in rows)


def test_직전_분기가_없으면_값을_만들지_않는다() -> None:
    rows = to_quarterly([_p(2025, "1Q", 100), _p(2025, "3Q", 330)])
    q3 = rows[1]
    assert q3["derived"] is True and q3["revenue"] is None and q3["opm"] is None
    assert q3["total_assets"] == 100.0


def test_연간_라벨_진행_연도() -> None:
    ps = [_p(2024, "1Q", 1), _p(2024, "2Q", 2), _p(2024, "3Q", 3), _p(2024, "4Q", 4),
          _p(2025, "1Q", 1), _p(2025, "2Q", 2), _p(2026, "1Q", 1), _p(2026, "2Q", 2),
          _p(2026, "3Q", 3), _p(2023, "1Q", 9)]  # fmt: skip
    labels = {a["year"]: (a["label"], a["months"], a["partial"]) for a in to_annual(ps)}
    assert labels == {2023: ("3M23", 3, True), 2024: ("2024", 12, False),
                      2025: ("1H25", 6, True), 2026: ("9M26", 9, True)}  # fmt: skip


class _Src:
    def __init__(self, data: dict[tuple[int, str, str], list[dict[str, Any]]]) -> None:
        self.data = data
        self.calls: list[tuple[int, str, str]] = []

    def financials(
        self, corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS"
    ) -> list[dict[str, Any]]:
        self.calls.append((year, reprt_code, fs_div))
        return self.data.get((year, reprt_code, fs_div), [])


def _row(acc_id: str, nm: str, sj: str, amount: str, add: str | None = None) -> dict[str, Any]:
    r = {"account_id": acc_id, "account_nm": nm, "sj_div": sj, "thstrm_amount": amount}
    if add is not None:
        r["thstrm_add_amount"] = add
    return r


def test_연결이_없으면_별도로_폴백하고_순서를_정렬한다() -> None:
    rev = "ifrs-full_Revenue"
    src = _Src({
        (2025, "11012", "OFS"): [_row(rev, "매출액", "IS", "60", "110")],
        (2025, "11013", "CFS"): [_row(rev, "매출액", "IS", "50", "50")],
    })  # fmt: skip
    ps = fetch_periods(src, "99000001", [2025], "CFS")
    assert [(p.quarter, p.fs_div, p.cumulative["revenue"]) for p in ps] == [
        ("1Q", "CFS", 50.0), ("2Q", "OFS", 110.0)]  # fmt: skip
    # 보고서 넷을 차례로(1Q·반기·3Q·사업), CFS 가 비면 OFS 를 한 번 더
    assert src.calls[:3] == [(2025, "11013", "CFS"), (2025, "11012", "CFS"), (2025, "11012", "OFS")]


def test_pick_은_태그_이름_순으로_고르고_누적_0_은_당기값으로_간다() -> None:
    """legacy 동작(D-P4-8 — 골든이 고정): `thstrm_add_amount` 0 은 `or` 로 3개월값이 된다."""
    rows = [
        _row("x", "영업이익", "IS", "7", "0"),
        _row("ifrs-full_Revenue", "매출", "IS", "9", "30"),
    ]
    assert _pick(rows, "revenue") == 30.0
    assert _pick(rows, "operating_income") == 7.0  # 누적 0 → 3개월값(legacy 버그 — 고정)
    assert _pick([_row("x", "(주)자산총계", "BS", "(1,000)")], "total_assets") == -1000.0
    assert _pick([_row("x", "자산 총계", "IS", "5")], "total_assets") == 5.0  # 3순위: 이름만
