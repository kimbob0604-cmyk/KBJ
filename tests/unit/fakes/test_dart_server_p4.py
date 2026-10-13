"""가짜 DART 의 P4 서빙 — tests/fakes/dart_server.FakeDart(world=…)(묶음 F, §1.3).

세계(`p4_world.P4World`)의 응답을 그대로 서빙하는지(접수 시각 ≤ 지금), 엔드포인트별 상태 주입·zip
엔드포인트의 XML 상태 응답, 키 검사, 세계가 없을 때 P2 동작이 그대로인지 본다. 호출은 정본
`DartClient` 로.
"""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest

from kbj.core.time import KST
from kbj.data.public.dart.client import DartClient, DartError, DartQuotaExceeded
from tests.fakes.dart_server import KEY, FakeDart
from tests.fixtures.synthetic.p4_world import CLOCK, build_world
from tests.unit.data.public._support import RecordingLimiter


def client_for(fake: FakeDart, key: str = KEY) -> DartClient:
    return DartClient(key, transport=fake.transport("t"), limiter=RecordingLimiter(), retries=1,
                      backoff_s=0)  # fmt: skip


def test_list_is_the_world_reply_with_the_default_page_count() -> None:
    w = build_world()
    fake = FakeDart(lambda: CLOCK, world=w)
    js = client_for(fake).get_json("list.json", {"bgn_de": "20261116"}, use_cache=False)
    assert js == w.dart_json("list.json", {"bgn_de": "20261116"}, now=CLOCK)
    assert js["page_count"] == 10 and len(js["list"]) == 10  # DART 기본 10건
    assert js["_source"].startswith("SYNTHETIC")
    rcepts = [x["rcept_no"] for x in js["list"]]
    posted = {d.rcept_no: d.posted_at for d in w.disclosures}
    assert [posted[r] for r in rcepts] == sorted((posted[r] for r in rcepts), reverse=True)


def test_list_hides_what_is_not_yet_received() -> None:
    w = build_world()
    at = datetime(2026, 11, 16, 9, 0, tzinfo=KST)
    fake = FakeDart(lambda: at, world=w)
    js = client_for(fake).get_json(
        "list.json", {"bgn_de": "20261116", "page_count": "100"}, use_cache=False
    )
    assert all(w.disclosure_by_rcept(x["rcept_no"]).posted_at <= at for x in js["list"])
    assert js["total_count"] == len(w.disclosures_on(w.today, now=at))


def test_document_zip_and_missing_document() -> None:
    w = build_world()
    c = client_for(FakeDart(lambda: CLOCK, world=w))
    rc = w.rcept("e14.adj2")
    files = c.document_texts(rc)
    assert files == list(w.documents(rc))
    assert c.document_texts("20990101000001") == []  # 013(XML) → 빈 목록
    early = FakeDart(lambda: datetime(2026, 11, 16, 9, 0, tzinfo=KST), world=w)
    assert client_for(early).document_texts(rc) == []  # 접수 전


def test_structured_and_ownership_and_shares_and_company() -> None:
    w = build_world()
    c = client_for(FakeDart(lambda: CLOCK, world=w))
    corp14 = w.by_no(14).company_corp
    cb = c.get_json("cvbdIsDecsn.json", {"corp_code": corp14, "bgn_de": "20240101",
                                         "end_de": "20261116"}, use_cache=False)  # fmt: skip
    assert len(cb["list"]) == 3
    ele = c.get_json("elestock.json", {"corp_code": w.by_no(15).company_corp}, use_cache=False)
    assert len(ele["list"]) == 6
    tot = c.get_json("stockTotqySttus.json", {"corp_code": corp14, "bsns_year": "2026",
                                              "reprt_code": "11014"}, use_cache=False)  # fmt: skip
    assert [r["se"] for r in tot["list"]] == ["보통주", "우선주", "합계"]
    co = c.get_json("company.json", {"corp_code": corp14}, use_cache=False)
    assert co["acc_mt"] == "12" and co["corp_code"] == corp14
    zipped = c.get_raw("corpCode.xml", {})
    assert zipfile.ZipFile(io.BytesIO(zipped)).namelist() == ["CORPCODE.xml"]


def test_inject_per_endpoint_and_xml_status_for_zip_endpoints() -> None:
    fake = FakeDart(lambda: CLOCK, world=build_world())
    c = client_for(fake)
    fake.inject("020", 1, endpoint="document.xml")
    c.get_json("list.json", {"bgn_de": "20261116"}, use_cache=False)  # 다른 엔드포인트는 그대로
    with pytest.raises(DartQuotaExceeded):
        c.document_texts("20261116800058")
    assert fake.calls("document.xml")[-1].status == "020"
    fake.inject("800", 1)
    with pytest.raises(DartError) as e:
        c.get_json("company.json", {"corp_code": "99400010"}, use_cache=False)
    assert e.value.code == "800"


def test_unknown_endpoint_and_wrong_key() -> None:
    w = build_world()
    fake = FakeDart(lambda: CLOCK, world=w)
    with pytest.raises(DartError) as e:
        client_for(fake).get_json("nope.json", {}, use_cache=False)
    assert e.value.status == 404
    with pytest.raises(DartError) as e2:
        client_for(fake, key="y" * 40).get_json("list.json", {}, use_cache=False)
    assert e2.value.code == "010" and e2.value.critical
    assert all("crtfc_key" not in s.params for s in fake.seen)


def test_without_a_world_the_p2_synthetic_replies_are_unchanged() -> None:
    fake = FakeDart(lambda: CLOCK)
    c = client_for(fake)
    js = c.get_json("list.json", {"bgn_de": "20261116"}, use_cache=False)
    assert js["page_count"] == 100 and len(js["list"]) <= 2
    assert (
        c.get_json("company.json", {"corp_code": "90000001"}, use_cache=False)["induty_code"]
        == "264"
    )
    with pytest.raises(DartError):
        c.get_json("elestock.json", {"corp_code": "90000001"}, use_cache=False)  # 404
