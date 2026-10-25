"""HS 코드 대조 CLI(kbj.services.collectors.hs_match) — 합성 계열만."""

from __future__ import annotations

import io
import json

from kbj.services.collectors.hs_match import Series, Target, find_subsets, main, match_target

M = ["2026-01", "2026-02", "2026-03"]
Sgg = dict[tuple[str, str], dict[str, dict[str, float]]]
GG = (("41", "수원시"), ("41", "화성시"))


def s(*vals: float) -> dict[str, float]:
    return dict(zip(M, vals, strict=True))


class FakeSource:
    def __init__(self, items: Series, sgg: Sgg | None = None):
        self._items = items
        self._sgg = sgg or {}
        self.calls: list[tuple[str, ...]] = []

    def items(self, hs4: str, start: str, end: str, flow: str) -> Series:
        self.calls.append(("i", hs4))
        return {c: v for c, v in self._items.items() if c.startswith(hs4)}

    def item_country(self, hs4: str, country: str, start: str, end: str, flow: str) -> Series:
        self.calls.append(("c", hs4, country))
        return {c: v for c, v in self._items.items() if c.startswith(hs4)}

    def sigungu(self, sido: str, hs6: str, start: str, end: str) -> dict[str, dict[str, float]]:
        self.calls.append(("s", sido, hs6))
        return self._sgg.get((sido, hs6), {})


ITEMS: Series = {
    "8542321010": s(100.0, 200.0, 300.0),
    "8542321030": s(50.0, 60.0, 70.0),
    "8542321090": s(0.0, 0.0, 0.0),  # 0 계열 — 조합에서 빠진다
    "8542390000": s(7.0, 8.0, 9.0),
}


def test_single_and_pair():
    assert find_subsets(ITEMS, s(100.0, 200.0, 300.0)) == [("8542321010",)]
    assert ("8542321010", "8542321030") in find_subsets(ITEMS, s(150.0, 260.0, 370.0))


def test_one_month_off_allowed_two_not():
    assert find_subsets(ITEMS, s(100.0, 200.0, 999.0)) == [("8542321010",)]
    assert find_subsets(ITEMS, s(100.0, 777.0, 999.0)) == []


def test_tolerance_relative():
    assert find_subsets(ITEMS, s(100.3, 200.5, 300.9)) == [("8542321010",)]


def test_national_match_and_nomatch():
    src = FakeSource(ITEMS)
    t = Target("R1", "exp", "national", s(57.0, 68.0, 79.0), ("8542321030",))
    r = match_target(t, src)
    assert r.status == "match" and r.codes == ("8542321030", "8542390000")
    t2 = Target("R2", "exp", "national", s(1.0, 1.0, 1.0), ("854232",))
    assert match_target(t2, src).status == "nomatch"


def test_sigungu_sums_regions_and_city_with_gu():
    sgg = {
        ("41", "848620"): {
            # API 는 시도를 붙여 온다('충청남도 아산시' — 2026-10-26 실측)
            "경기도 수원시 영통구": s(10.0, 10.0, 10.0),
            "경기도 화성시": s(5.0, 5.0, 5.0),
            "경기도 용인시": s(1.0, 1.0, 1.0),
        },
        ("41", "848640"): {"화성시": s(3.0, 3.0, 3.0)},
    }
    src = FakeSource({"8486201000": s(1.0, 1.0, 1.0), "8486401000": s(1.0, 1.0, 1.0)}, sgg)
    t = Target(
        "R3",
        "exp",
        "sigungu",
        s(15.0, 15.0, 15.0),
        ("8486201000",),
        regions=GG,
    )
    r = match_target(t, src)
    assert r.status == "match" and r.codes == ("848620",)
    t2 = Target(
        "R4",
        "exp",
        "sigungu",
        s(18.0, 18.0, 18.0),
        ("8486201000",),
        regions=GG,
    )
    assert match_target(t2, src).codes == ("848620", "848640")  # 같은 HS4 의 형제 HS6 도 본다


def test_sigungu_name_mismatch_reported():
    src = FakeSource(
        {"8486201000": s(1.0, 1.0, 1.0)}, {("41", "848620"): {"다른시": s(1.0, 1.0, 1.0)}}
    )
    t = Target(
        "R5", "exp", "sigungu", s(1.0, 1.0, 1.0), ("8486201000",), regions=(("41", "화성시"),)
    )
    r = match_target(t, src)
    assert r.status == "no_data" and "다른시" in r.note


def test_main_lines_without_amounts():
    body = {
        "targets": [
            {
                "rid": "R1",
                "dir": "exp",
                "kind": "national",
                "months": s(100.0, 200.0, 300.0),
                "candidates": ["8542321010"],
            }
        ]
    }
    out = io.StringIO()
    rc = main([], inp=io.StringIO(json.dumps(body)), out=out, source=lambda: FakeSource(ITEMS))
    lines = out.getvalue().splitlines()
    assert rc == 0
    assert lines[0].startswith("R1 match 8542321010")
    assert lines[-1] == "# match=1"
    assert "300" not in lines[0]  # 금액은 내지 않는다
    out = io.StringIO()
    main(["--jsonl"], inp=io.StringIO(json.dumps(body)), out=out, source=lambda: FakeSource(ITEMS))
    first, last = out.getvalue().splitlines()[0], out.getvalue().splitlines()[-1]
    got = json.loads(first)
    assert got["rid"] == "R1" and got["status"] == "match" and got["codes"][0] == "8542321010"
    assert set(got) == {"rid", "status", "codes", "err", "alts", "note"} and last == "# match=1"


def test_main_bad_input():
    out = io.StringIO()
    assert main([], inp=io.StringIO("{}"), out=out, source=lambda: FakeSource({})) == 2


def test_whole_prefix_group_is_short():
    many: Series = {f"870899{i:04d}": s(100.0, 100.0, 100.0) for i in range(12)}
    src = FakeSource(many)
    r = match_target(Target("R6", "exp", "national", s(1200.0, 1200.0, 1200.0), ("8708",)), src)
    assert r.status == "match" and len(r.codes) == 12
    assert r.line() == "R6 match 87089900* err=0.00%"


def test_sgg_base_drops_sido_prefix_only():
    from kbj.data.public.customs.models import sgg_base

    assert sgg_base("충청남도 아산시") == "아산시"
    assert sgg_base("경기도 수원시 영통구") == "수원시 영통구"
    assert sgg_base("인천광역시 서구") == "서구"
    assert sgg_base("강원특별자치도 원주시") == "원주시"
    assert sgg_base("수원시 영통구") == "수원시 영통구"
    # 세종은 시도 이름 하나로 온다 — 지역표·입력은 '세종시'(hs-match R114 no_data, 2026-10-26)
    assert sgg_base("세종특별자치시") == "세종시"
    assert sgg_base("세종시") == "세종시"
