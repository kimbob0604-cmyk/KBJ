"""DART 종목별 당일 공시 — kbj.data.public.dart.disclosures.disclosures_for · kind_of.

**승격**: ETF-Traker `board/tests/test_dart_for.py`(7개 — unittest → pytest 함수, 설계 §1.11).
단언은 그대로 두고, 바뀐 API 에 맞춘 부분만 고쳤다:

- import: `..ingest.dart as D` → `kbj.data.public.dart.disclosures as D`, `..ingest.http.Fetch` →
  `kbj.data.http.FetchError`(같은 이름 `Fetch` 로 받는다 — `DartError` 가 그 하위다).
- 모듈 전역(키·세션·`corp_codes()` 캐시 파일)을 쓰던 함수가 클라이언트를 인자로 받는다 →
  `mock.patch.object(D, 'corp_codes'|'_key'|'session'|'get')` 대신 진짜 `DartClient` 를 가짜
  전송(`httpx.MockTransport`)으로 만들고, `client.corp_map` 을 같은 값으로 바꾸고, `client.get_json`
  을 기록하며 통과시키는 감싸개로 바꾼다(원본 `fake_get` 이 남기던 url·params·timeout·retries 를
  남긴다).
  상태 코드(013·020) 처리는 이제 클라이언트가 하므로 응답은 HTTP 본문으로 준다.
- 키(`_key` → 'K')는 클라이언트 생성자에 넣는다. 파라미터의 `crtfc_key` 는 클라이언트가 붙이므로
  기록에 없다(원본 단언도 보지 않았다).
- 마지막 시험: ET `disclosures`(목록, 통신 확인용)는 `list_disclosures` 로 이름이 바뀌었다 — 그
  docstring 이 한계(`corp_cls='Y'`·`disclosures_for`)를 적어 두는지 그대로 본다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

import kbj.data.public.dart.disclosures as D
from kbj.data.http import FetchError as Fetch
from kbj.data.public.dart.client import DartClient
from tests.fakes.clock import FakeClock
from tests.unit.data.public._support import T0, RecordingLimiter


@dataclass
class Env:
    client: DartClient
    seen: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    reply: dict[str, Any] = field(default_factory=dict[str, Any])


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Env]:
    holder: dict[str, Env] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(holder["env"].reply).encode())

    clock = FakeClock(T0)
    client = DartClient(
        "K",
        limiter=RecordingLimiter(),
        transport=httpx.MockTransport(handler),
        now=clock,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    e = Env(client)
    holder["env"] = e
    e.reply = {
        "status": "000",
        "list": [
            {
                "report_nm": "단일판매ㆍ공급계약체결",
                "rcept_no": "20260921000123",
                "rcept_dt": "20260921",
                "flr_nm": "케이씨",
                "stock_code": "029460",
            },
            {"report_nm": "", "rcept_no": "x", "rcept_dt": "20260921"},
        ],
    }
    real_get_json = client.get_json

    def fake_get(
        endpoint: str,
        params: Mapping[str, Any],
        *,
        use_cache: bool = True,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        e.seen.append(
            {"url": endpoint, "params": dict(params), "timeout": timeout, "retries": retries}
        )
        return real_get_json(
            endpoint, params, use_cache=use_cache, timeout=timeout, retries=retries
        )

    monkeypatch.setattr(client, "corp_map", lambda: {"029460": "00126380"})
    monkeypatch.setattr(client, "get_json", fake_get)
    yield e
    client.close()


def test_mapping() -> None:
    cases = {
        "단일판매ㆍ공급계약체결": "contract",
        "자기주식취득 신탁계약 체결 결정": "buyback",  # contract 보다 먼저
        "유상증자결정": "capital",
        "주요사항보고서(무상증자결정)": "capital",
        "최대주주변경": "owner",
        "임원ㆍ주요주주특정증권등소유상황보고서": "owner",
        "투자판단관련주요경영사항 (임상 3상 승인)": "clinical",
        "연결재무제표기준영업(잠정)실적(공정공시)": "earnings",
        "조회공시요구(현저한시황변동)에 대한 답변": "inquiry",
        "기타경영사항(자율공시)": "other",
        "": "other",
        None: "other",
    }
    for title, kind in cases.items():
        assert D.kind_of(title) == kind, title


def test_asks_by_corp_code_for_the_one_day(env: Env) -> None:
    got = D.disclosures_for(env.client, "029460", "2026-09-21", timeout=8, retries=2)
    p = env.seen[0]["params"]
    assert p["corp_code"] == "00126380"
    assert (p["bgn_de"], p["end_de"]) == ("20260921", "20260921")
    assert "corp_cls" not in p, "시장 구분으로 거르지 않는다 — 코스닥이 빠진다"
    assert (env.seen[0]["timeout"], env.seen[0]["retries"]) == (8, 2)
    assert len(got) == 1
    r = got[0]
    assert r["title"] == "단일판매ㆍ공급계약체결"
    assert r["kind"] == "contract"
    assert r["publisher"] == "DART"
    assert r["date"] == "2026-09-21"
    assert r["link"] == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260921000123"
    assert r["filer"] == "케이씨"


def test_default_timeout_is_not_forced(env: Env) -> None:
    D.disclosures_for(env.client, "029460", "2026-09-21")
    assert env.seen[0]["timeout"] is None
    assert env.seen[0]["retries"] is None


def test_no_result_status_is_an_empty_list(env: Env) -> None:
    env.reply = {"status": "013", "message": "조회된 데이타가 없습니다."}
    assert D.disclosures_for(env.client, "029460", "2026-09-21") == []


def test_error_status_raises(env: Env) -> None:
    env.reply = {"status": "020", "message": "요청 제한을 초과하였습니다."}
    with pytest.raises(Fetch) as e:
        D.disclosures_for(env.client, "029460", "2026-09-21")
    assert "020" in str(e.value)


def test_unknown_code_raises_not_empty(env: Env) -> None:
    with pytest.raises(Fetch) as e:
        D.disclosures_for(env.client, "000000", "2026-09-21")
    assert "corp_code" in str(e.value)
    assert env.seen == []


def test_legacy_disclosures_docstring_states_its_limits() -> None:
    doc = D.list_disclosures.__doc__
    assert doc is not None
    assert "corp_cls='Y'" in doc
    assert "disclosures_for" in doc
