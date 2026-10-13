"""종류주식 → 회사(`prv_market.share_class`) — 코드·이름 규칙 둘 다 맞을 때만(R13)."""

from __future__ import annotations

from datetime import date

from kbj.core.quality import Quality
from kbj.core.rows import UniverseRow
from kbj.engines.valuation.classes import share_classes

D = date(2026, 11, 13)
IDX = {"994010": ("99400010", "모의전자"), "99K170": ("99400170", "모의로보")}


def u(code: str, name: str, kind: str | None = "common") -> UniverseRow:
    return UniverseRow(code, D, name, "KOSPI", kind, None, "krx", Quality.OK)


def test_우선주는_보통주_회사에_묶인다() -> None:
    sc = share_classes([u("994010", "모의전자"), u("994015", "모의전자우", "pref")], IDX)
    by = sc.by_code()
    assert by["994015"].share_class == "pref" and by["994015"].common_code == "994010"
    assert by["994015"].corp_code == "99400010"  # 보통주의 고유번호
    assert by["994010"].share_class == "common" and by["994010"].common_code == "994010"
    assert sc.groups == {"994010": ("994010", "994015")}
    assert sc.unmatched == {}


def test_2우B_같은_접미도_이름_규칙으로() -> None:
    sc = share_classes([u("994200", "모의차"), u("994207", "모의차2우B", "pref")], {})
    assert sc.by_code()["994207"].common_code == "994200"
    assert sc.by_code()["994207"].corp_code is None  # 고유번호 표에 없음


def test_코드나_이름이_어긋나면_짝_없음() -> None:
    sc = share_classes(
        [
            u("994010", "모의전자"),
            u("994015", "다른회사우", "pref"),
            u("994305", "없는회사우", "pref"),
        ],
        IDX,
    )
    assert set(sc.unmatched) == {"994015", "994305"}
    assert "이름 규칙" in sc.unmatched["994015"] and "코드 규칙" in sc.unmatched["994305"]
    assert sc.of("994015") is None
    assert sc.groups == {"994010": ("994010",)}


def test_스팩_리츠_종류모름은_혼자_회사_ETF_는_뺀다() -> None:
    rows = [
        u("994040", "모의스팩1호", "spac"),
        u("994050", "모의리츠", "reit"),
        u("99K170", "모의로보", None),
        u("994990", "모의200", "etf"),
    ]
    sc = share_classes(rows, IDX)
    assert set(sc.by_code()) == {"994040", "994050", "99K170"}
    assert all(r.share_class == "common" for r in sc.rows)
    assert sc.by_code()["99K170"].corp_code == "99400170"
