"""DART 공시 — 목록·종목별 당일 공시·주식 수 변동 공시·종류 분류·기업개황(공개 등급).

승격 원본: ETF-Traker `board/ingest/dart.py`(`STATUS_KO`:37, `_decode_status`:55, `_check`:67,
`company`:105, `disclosures`:116, `stock_actions`:152, `KINDS`:183, `kind_of`:196,
`disclosures_for`:210). 키·세션·캐시 파일을 모듈 전역으로 들던 것을 **클라이언트 인자**로 바꿨다
(`DartClient` — 키·리미터·예산·상태 코드 처리는 거기). 반환 모양(dict 키)은 ET 그대로 —
legacy 가 다시 내보내 쓴다(묶음 H).

- 실패는 예외로 올린다. 빈 리스트는 '그런 공시가 없었다'(013 포함)이고 조회 실패와 다르다.
- 종목코드 → corp_code 를 못 찾으면 부르기 **전에** 실패한다(요청 0건).
- 공시 링크는 공개 뷰어 주소(`dart.fss.or.kr/dsaf001`)다 — API 주소가 아니다.

P4(묶음 F — docs/p4_design.md §1.3): 증분 피드(`filings.dart_feed`)가 저장·파싱 경로를
고르는 **잘게 나눈 종류** `FEED_KINDS`·`feed_kind()` 를 더했다. 설계는 이 종류들을 `KINDS` 앞에
넣으라 했지만 그러면 `kind_of` 의 결과가 바뀐다 — 승격 원본 시험(ET
`test_dart_for.py::test_mapping` — 잠정실적 → earnings, 소유보고 → owner)이 깨지고 legacy board
표시(다시 내보내 쓴다)가 바뀐다. 그래서 `KINDS`·`kind_of` 는 그대로 두고(반환 dict 키·값 모두 P2
그대로), 피드는 `feed_kind()`(잘게 나눈 종류 먼저, 안 걸리면 `kind_of`)를 쓴다.
`pub_filings.disclosure.kind` 에 들어가는 값이 `feed_kind()` 다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from kbj.data.public.dart.client import (
    SOURCE,
    STATUS_KO,
    DartError,
    decode_status,
    describe_status,
)

__all__ = [
    "ACTION_WINDOW",
    "CORP_ACTION",
    "FEED_KINDS",
    "KINDS",
    "PUBLISHER",
    "STATUS_KO",
    "Disclosure",
    "DisclosurePage",
    "company",
    "decode_status",
    "describe_status",
    "disclosures_for",
    "feed_kind",
    "kind_of",
    "list_disclosures",
    "stock_actions",
]

PUBLISHER: Final = "DART"
_VIEWER: Final = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

# 주가 시계열에 계단을 만드는 기업행위. 제목으로 거른다(ET split_guard 의 짝 — 점프가 분할·병합
# 때문인지 그냥 크게 움직인 것인지는 시세만 봐서는 갈리지 않는다. 공시는 답을 갖고 있다)
CORP_ACTION: Final = ("분할", "병합", "감자", "무상증자", "액면")
# 공시는 사건 당일에만 나오지 않는다. 결정 공시가 앞서고 변경상장이 뒤따른다
ACTION_WINDOW: Final = 400

# 공시 제목 → 종류. 앞에 있는 것이 우선이다 — '자기주식취득 신탁계약 체결' 은 buyback 이지
# contract 가 아니다. 어디에도 안 걸리면 'other'. 종류는 표기를 고르는 데만 쓴다 — 판정은
# 하지 않는다
KINDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("inquiry", ("조회공시", "풍문", "현저한시황변동", "시황변동")),
    ("buyback", ("자기주식", "자사주", "주식소각")),
    (
        "capital",
        (
            "유상증자",
            "무상증자",
            "전환사채",
            "신주인수권부사채",
            "교환사채",
            "감자",
            "액면",
            "주식분할",
            "주식병합",
            "합병",
            "분할",
        ),
    ),
    (
        "owner",
        ("최대주주", "주식등의대량보유", "임원ㆍ주요주주", "임원·주요주주", "경영권", "지분"),
    ),
    # '허가'·'승인' 만으로는 펀드명 '특허가치'·'변경승인' 등에 걸렸다(2026-10-20) — 맥락 있는 말만
    ("clinical", ("임상", "품목허가", "품목승인", "시판허가", "판매허가", "허가신청", "시판승인")),
    ("earnings", ("영업(잠정)실적", "잠정실적", "매출액또는손익", "실적", "결산")),
    ("contract", ("공급계약", "단일판매", "수주", "판매ㆍ공급", "판매·공급", "계약")),
)


# 피드 종류(P4) — `feed_kind()` 가 공백을 지운 제목에서 앞에서부터 찾는다. 안 걸리면
# `kind_of()`(위 KINDS). 저장·파싱 경로를 고르는 데 쓴다(docs/p4_design.md §1.3·§4.1):
#   pre_announce       결산실적공시예고 — 원문(예정일) [확인 필요 #30]
#   prelim             영업(잠정)실적 — 원문(SD classify_disclosure_type:58 순서). 자회사 잠정실적도
#                      prelim(범위 subsidiary — 알림 대상 아님). '실적등에대한전망'은 안 걸린다
#                      (→ kind_of 의 earnings)
#   overhang_exercise  전환청구권·신주인수권·교환청구권 행사 — 원문(구조화 API 없음 [확인 필요 #31])
#   price_adjust       전환가액·행사가액·교환가액 조정 — 원문 [확인 필요 #31]
#   overhang           CB·BW·EB 발행결정, 유상·무상·유무상증자결정 — 구조화 API 우선, 빈 칸은 원문
#   buyback            자기주식 취득·처분 결정(신탁 계약은 종류만 — 구조화 데이터셋이 카탈로그에
#                      없다)
#   insider            임원ㆍ주요주주특정증권등소유상황보고서 — elestock(회사 보고 전체) + 원문
#                      변동 사유
#   major_holder       주식등의대량보유상황보고서 — majorstock
#   periodic           사업·반기·분기보고서 — 파싱하지 않는다(fin.quarterly 가 재무를 받는다)
FEED_KINDS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("pre_announce", ("결산실적공시예고",)),
    ("prelim", ("연결재무제표기준영업(잠정)", "영업(잠정)실적")),
    ("overhang_exercise", ("전환청구권행사", "신주인수권행사", "교환청구권행사")),
    (
        "price_adjust",
        (
            "전환가액의조정",
            "전환가액조정",
            "행사가액의조정",
            "행사가액조정",
            "교환가액의조정",
            "교환가액조정",
        ),
    ),
    (
        "overhang",
        (
            "전환사채권발행결정",
            "전환사채발행결정",
            "신주인수권부사채권발행결정",
            "신주인수권부사채발행결정",
            "교환사채권발행결정",
            "교환사채발행결정",
            "유무상증자결정",
            "유상증자결정",
            "무상증자결정",
        ),
    ),
    (
        "buyback",
        ("자기주식취득결정", "자기주식처분결정", "자기주식취득신탁계약", "자기주식처분신탁계약"),
    ),
    (
        "insider",
        ("임원ㆍ주요주주특정증권등소유상황보고서", "임원·주요주주특정증권등소유상황보고서"),
    ),
    ("major_holder", ("주식등의대량보유상황보고서",)),
    ("periodic", ("사업보고서", "반기보고서", "분기보고서")),
)


class DartJson(Protocol):
    """공시 함수가 쓰는 클라이언트 모양(`DartClient` 가 맞는다 — 시험은 가짜로 바꿔 끼운다)."""

    def corp_map(self) -> dict[str, str]: ...

    def get_json(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        use_cache: bool = True,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]: ...


# 종류 판정 전에 지울 말 — 다른 종류의 낱말을 품었지만 그 뜻이 아니다(2026-10-20 점검: 증권발행실적
# 보고서가 '실적'으로, 증권신고서(지분증권)가 '지분'으로 분류됐다)
_NOT_KIND: Final = ("증권발행실적", "지분증권", "특허가치")


def _clean(title: str | None) -> str:
    t = (title or "").replace("ㆍ", "·")
    for w in _NOT_KIND:
        t = t.replace(w, "")
    return t


def kind_of(title: str | None) -> str:
    """공시 제목의 종류. `KINDS` 순서대로 처음 걸리는 것."""
    t = _clean(title)
    for kind, words in KINDS:
        if any(w in t for w in words):
            return kind
    return "other"


def feed_kind(title: str | None) -> str:
    """피드 종류(P4) — `FEED_KINDS`(공백을 지운 제목) 먼저, 안 걸리면 `kind_of`."""
    t = (title or "").replace(" ", "").replace("ㆍ", "·")
    if "가액" in t and "조정" in t:
        return "price_adjust"  # '전환가액·행사가액 조정' 처럼 표기가 섞여도(2026-10-20 점검)
    for kind, words in FEED_KINDS:
        if any(w in t for w in words):
            return kind
    return kind_of(title)


def _ymd(d: object) -> str:
    s = str(d or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 else s


def _de(day: str) -> str:
    """'YYYY-MM-DD' 또는 'YYYYMMDD' → 'YYYYMMDD'."""
    s = day.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise ValueError(f"날짜는 YYYY-MM-DD: {day!r}")
    return s


def _link(rcept_no: object) -> str:
    return f"{_VIEWER}{rcept_no}"


def _corp_of(client: DartJson, code: str) -> str:
    corp = client.corp_map().get(code)
    if not corp:
        raise DartError("", f"{code} 의 corp_code 를 못 찾았다", "corpCode.xml")
    return corp


@dataclass(frozen=True)
class Disclosure:
    """공시 한 건(list.json 한 행)."""

    corp_code: str
    corp_name: str
    stock_code: str | None
    corp_cls: str  # Y 유가·K 코스닥·N 코넥스·E 기타
    title: str
    rcept_no: str
    filer: str | None
    date: str  # YYYY-MM-DD (접수 시각은 응답에 없다)
    remark: str
    source: str = SOURCE

    @property
    def link(self) -> str:
        return _link(self.rcept_no)

    @property
    def kind(self) -> str:
        return kind_of(self.title)


@dataclass(frozen=True)
class DisclosurePage:
    """공시목록 한 쪽."""

    page_no: int
    total_page: int
    total_count: int
    items: list[Disclosure] = field(default_factory=list[Disclosure])
    source: str = SOURCE

    def by_stock(self) -> dict[str, list[dict[str, Any]]]:
        """종목코드별 `{title, date, url, filer}`(ET `disclosures` 의 by_stock 모양)."""
        out: dict[str, list[dict[str, Any]]] = {}
        for d in self.items:
            if d.stock_code:
                out.setdefault(d.stock_code, []).append(
                    {"title": d.title, "date": d.date, "url": d.link, "filer": d.filer}
                )
        return out


def _int(v: object, default: int) -> int:
    s = str(v if v is not None else "").strip()
    return int(s) if s.isdigit() else default


def list_disclosures(
    client: DartJson,
    bgn: str,
    end: str | None = None,
    page: int = 1,
    count: int = 100,
    *,
    corp_cls: str | None = None,
) -> DisclosurePage:
    """기간 공시목록 **한 쪽**(`page`). 뒷장은 `total_page` 를 보고 부르는 쪽이 넘긴다.

    ET `disclosures` 의 한계 둘을 적어 둔다: 그 함수는 `corp_cls='Y'` 라 코스피만 받았고
    한 장(100건)만 받았다. 이 함수는 시장 구분을 주지 않으면(`corp_cls=None`) 전 시장이고,
    한 장씩 받는다. 종목별 당일 공시 수집은 이 함수가 아니라 종목의 corp_code 로 묻는
    `disclosures_for()` 를 쓴다.
    """
    if not 1 <= count <= 100:
        raise ValueError("count(page_count)는 1~100")
    params: dict[str, Any] = {
        "bgn_de": _de(bgn),
        "end_de": _de(end or bgn),
        "page_no": page,
        "page_count": count,
    }
    if corp_cls is not None:
        params["corp_cls"] = corp_cls
    js = client.get_json("list.json", params, use_cache=False)
    items = [
        Disclosure(
            corp_code=str(x.get("corp_code") or "").strip(),
            corp_name=str(x.get("corp_name") or "").strip(),
            stock_code=(str(x.get("stock_code") or "").strip() or None),
            corp_cls=str(x.get("corp_cls") or "").strip(),
            title=str(x.get("report_nm") or "").strip(),
            rcept_no=str(x.get("rcept_no") or "").strip(),
            filer=(str(x.get("flr_nm") or "").strip() or None),
            date=_ymd(x.get("rcept_dt")),
            remark=str(x.get("rm") or "").strip(),
        )
        for x in js.get("list") or []
    ]
    return DisclosurePage(
        page_no=_int(js.get("page_no"), page),
        total_page=_int(js.get("total_page"), 1 if items else 0),
        total_count=_int(js.get("total_count"), len(items)),
        items=items,
    )


def company(client: DartJson, code: str) -> dict[str, Any]:
    """기업개황. 표준산업분류코드(induty_code)가 여기 있다."""
    corp = _corp_of(client, code)
    js = client.get_json("company.json", {"corp_code": corp}, use_cache=False)
    return {
        "code": code,
        "corp_code": corp,
        "name": js.get("corp_name"),
        "induty_code": js.get("induty_code"),
        "source": SOURCE.lower(),
    }


def stock_actions(client: DartJson, code: str, bgn: str, end: str) -> list[dict[str, str]]:
    """한 종목의 기간 공시 중 주식 수가 바뀌는 사건만. 반환 `[{title, date, url}]` 날짜 오름차순.

    빈 리스트는 '그런 공시가 없었다' 이고, 그건 조회 실패와 다르다.
    """
    corp = _corp_of(client, code)
    js = client.get_json(
        "list.json",
        {
            "corp_code": corp,
            "bgn_de": _de(bgn),
            "end_de": _de(end),
            "page_no": 1,
            "page_count": 100,
        },
        use_cache=False,
    )
    out: list[dict[str, str]] = []
    for x in js.get("list") or []:
        title = str(x.get("report_nm") or "").strip()
        if not any(w in title for w in CORP_ACTION):
            continue
        out.append(
            {"title": title, "date": _ymd(x.get("rcept_dt")), "url": _link(x.get("rcept_no"))}
        )
    out.sort(key=lambda r: r["date"])
    return out


def disclosures_for(
    client: DartJson,
    code: str,
    asof: str,
    *,
    timeout: float | None = None,
    retries: int | None = None,
) -> list[dict[str, Any]]:
    """한 종목의 **기준일 당일** 공시. corp_code 로 묻는다 — 시장 구분과 무관하다.

    반환 `[{title, link, date, publisher:'DART', kind, filer}]`. 빈 리스트는 '그날 공시가 없었다'
    이고 조회 실패와 다르다 — 실패는 예외로 올린다. 접수 **시각**은 응답에 없다(rcept_dt 는 날짜뿐).
    그래서 소비자는 수집 시각을 '공시는 HH:MM 접수분까지' 로 적는다 — 그 뒤 접수분은 못 본 것이다.
    `timeout`·`retries` 는 클라이언트 기본값을 그 호출에서만 바꾼다. None 이면 기본값이다.
    """
    corp = _corp_of(client, code)
    ymd = _de(asof)
    js = client.get_json(
        "list.json",
        {"corp_code": corp, "bgn_de": ymd, "end_de": ymd, "page_no": 1, "page_count": 100},
        use_cache=False,
        timeout=timeout,
        retries=retries,
    )
    out: list[dict[str, Any]] = []
    for x in js.get("list") or []:
        title = str(x.get("report_nm") or "").strip()
        if not title:
            continue
        out.append(
            {
                "title": title,
                "link": _link(x.get("rcept_no")),
                "date": _ymd(x.get("rcept_dt")),
                "publisher": PUBLISHER,
                "kind": kind_of(title),
                "filer": str(x.get("flr_nm") or "").strip() or None,
            }
        )
    return out
