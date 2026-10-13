"""기업개황 — kbj.data.public.dart.company(묶음 F, §1.3·§3.3).

결산월(`acc_mt` — [확인 필요 #34: 형식 '03'·'12'])·업종·법인구분·설립일만 넣고 개인·연락 칸은
버린다.
"""

from __future__ import annotations

from datetime import date

import pytest

from kbj.core.quality import Quality
from kbj.data.public.dart import company as C
from kbj.data.public.dart.client import DartClient
from kbj.data.public.dart.doctext import ParseError
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import CLOCK, build_world
from tests.unit.data.public._support import RecordingLimiter


def test_world_profiles_equal_truth_through_the_client() -> None:
    w = build_world()
    fake = FakeDart(lambda: CLOCK, world=w)
    c = DartClient(KEY, transport=fake.transport("t"), limiter=RecordingLimiter(), retries=1)
    for p in w.profiles:
        assert C.fetch_profile(c, p.corp_code) == p
    reit = C.fetch_profile(c, w.by_no(7).corp_code or "")
    assert reit is not None and reit.acc_mt == 3  # '03' → 3
    assert C.fetch_profile(c, "99999999") is None  # 013
    assert all(s.params.keys() == {"corp_code"} for s in fake.calls("company.json"))


def test_private_fields_are_dropped() -> None:
    js = {"status": "000", "corp_code": "99400010", "corp_name": "모의전자", "stock_code": "994010",
          "ceo_nm": "가상대표", "adres": "주소", "phn_no": "02-000", "acc_mt": "12",
          "induty_code": "26429", "corp_cls": "Y", "est_dt": "19850302"}  # fmt: skip
    p = C.parse_profile(js, "99400010")
    assert p is not None
    text = repr(p)
    assert "가상대표" not in text and "주소" not in text and "02-000" not in text
    assert (p.acc_mt, p.est_dt, p.quality) == (12, date(1985, 3, 2), Quality.OK)


@pytest.mark.parametrize(
    ("patch", "field", "value"),
    [
        ({"acc_mt": "13"}, "acc_mt", None),
        ({"acc_mt": "x"}, "acc_mt", None),
        ({"induty_code": "26A"}, "induty_code", None),
        ({"corp_cls": "Z"}, "corp_cls", None),
        ({"stock_code": "12"}, "stock_code", None),
    ],
)
def test_odd_values_become_none_and_estimated(
    patch: dict[str, str], field: str, value: object
) -> None:
    js = {"status": "000", "corp_code": "99400010", "acc_mt": "12", "induty_code": "26429",
          "corp_cls": "Y", "stock_code": "994010", **patch}  # fmt: skip
    p = C.parse_profile(js, "99400010")
    assert p is not None and getattr(p, field) == value and p.quality is Quality.ESTIMATED


def test_other_company_in_reply_is_an_error() -> None:
    with pytest.raises(ParseError):
        C.parse_profile({"status": "000", "corp_code": "99400020"}, "99400010")
    assert C.parse_profile({"status": "013", "list": []}, "99400010") is None
