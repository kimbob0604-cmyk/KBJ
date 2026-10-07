"""공공데이터포털(data.go.kr) 공통 전송층 — 공개·로그인 어댑터가 함께 쓴다(설계 §1.4·§2·§4.2).

승격 원본: ETF-Traker `board/ingest/datago.py`(`key_forms`:46, `call`:67, `_rows`:85)
+ 게이트웨이 오류 코드(`docs/probe_results.md` §1, DG:15100475 "오픈API 에러코드 안내").

- 이 모듈은 **데이터를 담지 않는다**. 관세청·금투협 종합통계(공개 — kbj.data.public.customs·
  fsc_kofia_stats)와 금융위 주식·지수 시세(로그인 — kbj.data.private.fsc_*)가 같은 `serviceKey`
  (`KBJ_DATAGO_KEY`)로 이 전송층을 부른다. 등급은 부르는 어댑터의 폴더가 정한다(DATA_TIERS §3).
- **키 두 형태**: 포털은 같은 키를 Encoding·Decoding 두 형태로 보여 준다. 퍼센트 인코딩된
  키를 그대로 넣으면 httpx 가 한 번 더 인코딩해 깨진다. 값을 로그에 남기지 않고 고르려고
  '디코딩 → 그대로' 순서로 눌러 보고, 통한 형태(이름만)를 기억한다(ET 방식). 다음 형태로 넘어가는
  것은 **키 오류일 때만**(GW `30` 미등록 키·HTTP 401) — 다른 오류는 형태와 무관해 바로 올린다.
- **한도는 데이터셋(포털 ID)마다**: 초당 리미터 `rl:datago:<ID 해시>`·일 예산
  `budget:datago:<ID>:<KST 날짜>`(config/limits.yaml `datago` — 25/s, 9,500/일). 부를 때마다
  (키 형태를 바꿔 다시 부를 때도) 리미터 허가와 예산 한 몫을 받는다 — 실패한 호출도 센다.
- **게이트웨이 오류**(`parse_gw_error`): 포털은 오류일 때 `resultType=json` 을 무시하고 XML 봉투
  (`OpenAPI_ServiceResponse/cmmMsgHeader/returnReasonCode`)를 준다. HTTP 상태와 무관하게 본문을
  먼저 본다(2026-09-01 403 회귀 — ET test_http_reason).
    - `22` 일 한도 초과 → 그 데이터셋 예산을 그날 닫고(`DailyBudget.exhaust`)
      `DatagoQuotaExceeded`
    - `23` 초당 한도 초과 → 리미터 감속(`on_rate_limited`) 뒤 다시, 끝까지 안 되면
      `DatagoThrottled`
    - `30`·`31`·`21` 키 → 다른 키 형태, 다 안 되면 `DatagoKeyError`(`critical=True` —
      부르는 쪽이 health 를 낸다)
    - `12` 서비스 없음·폐기 → `DatagoGone`, `20`·`32`·`33` 접근 거부 → `DatagoAccessDenied`
      (활용신청 확인)
    - `05` 기관 응답 시간 초과 → 다시 시도(3·6·12초 — probe_results §1 연결 끊김 관찰)
- **기관 오류**(본문 `header/resultCode`): `00` 정상, `03` NODATA → 빈 결과 [추정], 그 밖
  (관세청 `99` 필수 누락·조회기간 초과 등) → `DatagoAgencyError`.
- 오류 문구에는 키가 없다: 키의 모든 형태(원문·디코딩·URL 인코딩)와 `serviceKey=` 꼴을 지운 뒤
  `FetchError` 로 올린다(절대 규칙 5). 키 값·길이는 어디에도 남기지 않는다.
- 이 모듈은 kbj.services 를 모른다(계약 ⑥) — health 는 부르는 쪽이 예외를 보고 낸다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Final, Literal
from urllib.parse import quote, quote_plus, unquote
from xml.etree.ElementTree import Element

import httpx
from defusedxml.ElementTree import fromstring as _xml_fromstring
from pydantic import SecretStr
from redis import Redis

from kbj.core.masking import MASK
from kbj.core.time import utcnow
from kbj.data.budget import DailyBudget
from kbj.data.http import BODY_SCAN, FetchError, get_capped, make_client, scrub, why
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "DATAGO"
LIMITS_SOURCE: Final = "datago"
# 공공데이터포털 게이트웨이. 이 주소는 이 파일과 공개·로그인 data.go.kr 어댑터에만 둔다
# (scripts/check_canonical.py 그룹 `datago` — 설계 §9.1)
_BASE: Final = "https://apis.data.go.kr"
_DATASET_ID = re.compile(r"[0-9]{6,10}")

CONNECT_S: Final = 5.0
TIMEOUT_S: Final = 20.0
MAX_BYTES: Final = 32 * 1024 * 1024  # 관세청 전 품목 응답도 넉넉히 — 넘으면 잘린 것으로 보고 실패
RETRY_WAITS_S: Final = (3.0, 6.0, 12.0)  # probe_results §1: 3·6·12초 재시도로 대부분 회복
KEY_PARAM: Final = "serviceKey"

Want = Literal["json", "xml"]


class GwAction(StrEnum):
    """게이트웨이 오류를 받았을 때 전송층이 하는 일."""

    QUOTA = "quota"  # 일 한도 — 그날 예산 닫기
    THROTTLE = "throttle"  # 초당 한도 — 감속 뒤 다시
    KEY = "key"  # 키 문제 — 다른 키 형태, 다 안 되면 critical
    GONE = "gone"  # 서비스 없음·폐기
    DENIED = "denied"  # 접근 거부(활용신청·IP)
    RETRY = "retry"  # 기관 응답 시간 초과 — 다시
    FAIL = "fail"  # 그 밖 — 다시 불러도 같다


# 포털 공통 게이트웨이 오류 코드. 22·23·30·31·12·05 는 DG:15100475 "오픈API 에러코드 안내"
# (probe_results §1), 나머지는 포털 공통 표 [추정 — 키 받은 뒤 실측]
GW_CODES: Final[Mapping[str, tuple[str, str, GwAction]]] = {
    "01": ("APPLICATION_ERROR", "어플리케이션 에러", GwAction.FAIL),
    "02": ("DB_ERROR", "데이터베이스 에러", GwAction.RETRY),
    "03": ("NODATA_ERROR", "데이터 없음", GwAction.FAIL),
    "04": ("HTTP_ERROR", "HTTP 에러", GwAction.RETRY),
    "05": ("SERVICETIME_OUT", "기관 응답 시간 초과", GwAction.RETRY),
    "10": ("INVALID_REQUEST_PARAMETER_ERROR", "잘못된 요청 파라미터", GwAction.FAIL),
    "11": ("NO_MANDATORY_REQUEST_PARAMETERS_ERROR", "필수 요청 파라미터 없음", GwAction.FAIL),
    "12": ("NO_OPENAPI_SERVICE_ERROR", "오픈API 서비스가 없거나 폐기됨", GwAction.GONE),
    "20": ("SERVICE_ACCESS_DENIED_ERROR", "서비스 접근 거부(활용신청 확인)", GwAction.DENIED),
    "21": ("TEMPORARILY_DISABLE_THE_SERVICEKEY_ERROR", "일시적으로 쓸 수 없는 키", GwAction.KEY),
    "22": (
        "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
        "일일 요청 한도 초과",
        GwAction.QUOTA,
    ),
    "23": (
        "LIMITED_NUMBER_OF_SERVICE_REQUESTS_PER_SECOND_EXCEEDS_ERROR",
        "초당 요청 한도 초과",
        GwAction.THROTTLE,
    ),
    "30": ("SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "등록되지 않은 서비스 키", GwAction.KEY),
    "31": ("DEADLINE_HAS_EXPIRED_ERROR", "기한이 끝난 서비스 키", GwAction.KEY),
    "32": ("UNREGISTERED_IP_ERROR", "등록되지 않은 IP", GwAction.DENIED),
    "33": ("UNSIGNED_CALL_ERROR", "서명되지 않은 호출", GwAction.DENIED),
    "99": ("UNKNOWN_ERROR", "기타 에러", GwAction.FAIL),
}
_GW_BY_NAME: Final = {name: code for code, (name, _, _) in GW_CODES.items()}
_ACTIONABLE: Final = frozenset(
    {GwAction.QUOTA, GwAction.THROTTLE, GwAction.KEY, GwAction.GONE, GwAction.DENIED}
)

# 오류 봉투의 태그. 값 자리는 `[^<]*` 하나뿐 — 겹치는 수량자를 두지 않는다(kbj.data.http 머리말)
_GW_TAG = re.compile(r"<(returnReasonCode|returnAuthMsg|errMsg)>([^<]*)</\1>")
_ENVELOPE = "OpenAPI_ServiceResponse"
# 기관 봉투 `resultMsg` 에 게이트웨이 오류 이름이 담겨 오는 경우. 기관 `03 NODATA_ERROR` 처럼 기관
# 코드와 이름이 겹치는 것은 기관 오류로 둔다 — 여기서는 전송층이 따로 할 일이 있는 갈래만 본다
_RESULT_MSG = re.compile(
    r'<resultMsg>\s*([A-Z_]+)\s*</resultMsg>|"resultMsg"\s*:\s*"\s*([A-Z_]+)\s*"'
)
# 기관 정상 코드. 포털 서비스마다 `00`·`0`·`000` 을 섞어 쓴다
_AGENCY_OK: Final = frozenset({"00", "0", "000"})
_AGENCY_NODATA: Final = frozenset({"03"})


@dataclass(frozen=True)
class GwError:
    """게이트웨이 오류 하나. `code` 는 두 자리 문자열(`"22"`)."""

    code: str
    name: str  # returnAuthMsg (예: LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR)
    message: str  # errMsg 또는 우리 설명

    @property
    def action(self) -> GwAction:
        hit = GW_CODES.get(self.code)
        return hit[2] if hit else GwAction.FAIL

    @property
    def described(self) -> str:
        """사람이 읽는 한 줄 — `GW 22 LIMITED_… (일일 요청 한도 초과)`."""
        hit = GW_CODES.get(self.code)
        ko = hit[1] if hit else "알 수 없는 코드"
        return f"GW {self.code} {self.name or '-'} ({ko})"


class DatagoError(FetchError):
    """공공데이터포털 호출 실패. 문구에 키가 없다. `gw` 는 게이트웨이 오류(있으면)."""

    critical: bool = False
    retryable: bool = False

    def __init__(
        self, dataset_id: str, status: int | None, reason: str, gw: GwError | None = None
    ) -> None:
        self.dataset_id = dataset_id
        self.gw = gw
        super().__init__(SOURCE, dataset_id, status, reason)


class DatagoQuotaExceeded(DatagoError):
    """GW `22` — 그 데이터셋의 그날 예산을 닫았다(내일 다시)."""


class DatagoThrottled(DatagoError):
    """GW `23`(또는 HTTP 429) — 감속하고 다시 불러도 안 됐다. 다음 주기에 다시."""

    retryable = True


class DatagoKeyError(DatagoError):
    """키 문제(GW `30`·`31`·`21`, HTTP 401) — 모든 키 형태가 거절됐다. 운영 critical."""

    critical = True


class DatagoGone(DatagoError):
    """GW `12` — 서비스가 없거나 폐기됐다(예: 15094783 소매채권, probe_results F1)."""

    critical = True


class DatagoAccessDenied(DatagoError):
    """GW `20`·`32`·`33` — 활용신청 안 한 데이터셋·IP·서명. 운영 critical."""

    critical = True


# 기관 오류 문구에 긴 토큰이 섞여 오면(요청 URL·키를 되돌려 보내는 기관) 통째로 가린다. 기관 문구는
# 짧은 사람 말이라 32자 넘는 base64·hex·퍼센트 인코딩 덩어리는 정상 내용이 아니다(절대 규칙 5).
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+/=%_-]{32,}")


def _safe_agency_message(message: str) -> str:
    return _LONG_TOKEN.sub(MASK, scrub(message))


class DatagoAgencyError(DatagoError):
    """기관 응답의 `header/resultCode` 오류(관세청 `99` 조회기간 초과 등) — 요청을 고쳐야 한다.

    기관 문구는 키를 가린 뒤에만 담는다(`_safe_agency_message`).
    """

    def __init__(self, dataset_id: str, code: str, message: str) -> None:
        safe = _safe_agency_message(message)
        self.code = code
        self.agency_message = safe
        super().__init__(dataset_id, 200, f"기관 resultCode={code} {safe}".strip())


class DatagoFormatError(DatagoError):
    """응답 모양이 기대와 다르다(본문 없음·JSON/XML 아님·봉투 없음) — 형식이 바뀐 것으로 본다."""


def parse_gw_error(text: str) -> GwError | None:
    """본문에서 게이트웨이 오류를 찾는다. 없으면 None.

    - XML 봉투 `OpenAPI_ServiceResponse/cmmMsgHeader`(`returnReasonCode`·`returnAuthMsg`·`errMsg`)
    - 기관 봉투의 `resultMsg` 가 게이트웨이 오류 이름일 때(일부 서비스가 JSON 헤더에 담는다
      [추정])
    """
    body = (text or "")[:BODY_SCAN]
    tags = {k: v.strip() for k, v in _GW_TAG.findall(body)}
    code = tags.get("returnReasonCode", "")
    name = tags.get("returnAuthMsg", "")
    if code or (name and name in _GW_BY_NAME) or _ENVELOPE in body:
        if not code and name in _GW_BY_NAME:
            code = _GW_BY_NAME[name]
        if not code and not name:
            return GwError("99", "", tags.get("errMsg", "") or "봉투만 있고 코드 없음")
        code = code.zfill(2) if code.isdigit() else code
        known = GW_CODES.get(code)
        return GwError(code, name or (known[0] if known else ""), tags.get("errMsg", ""))
    for m in _RESULT_MSG.finditer(body):
        gw_name = m.group(1) or m.group(2) or ""
        gw_code = _GW_BY_NAME.get(gw_name)
        if gw_code is not None and GW_CODES[gw_code][2] in _ACTIONABLE:
            return GwError(gw_code, gw_name, "")
    return None


def key_forms(raw: str) -> list[tuple[str, str]]:
    """시도할 키 형태 `[(이름, 값)]` — 통할 만한 것부터(ET `key_forms`).

    퍼센트 인코딩이 들어 있으면(포털 'Encoding' 형태) 푼 쪽이 먼저다 — httpx 가 파라미터를 한 번 더
    인코딩하므로 인코딩된 값을 그대로 보내면 `%252B` 가 되어 깨진다. 이름(`디코딩`·`그대로`)만
    밖으로 나간다.
    """
    raw = raw.strip()
    if not raw:
        raise ValueError("공공데이터포털 키가 비었다")
    forms: list[tuple[str, str]] = []
    dec = unquote(raw)
    if dec != raw:
        forms.append(("디코딩", dec))
    forms.append(("그대로", raw))
    return forms


def unwrap_items(js: Any, dataset_id: str = "") -> tuple[list[dict[str, Any]], int]:
    """포털 공통 JSON 봉투(`response.header`·`response.body.items.item`)를 벗긴다(ET `_rows`).

    `item` 이 하나면 dict 로, 없으면 빈 문자열로 오는 서비스가 있어 둘 다 목록으로 바꾼다.
    `totalCount` 가 없으면 0. 기관 오류 코드는 `DatagoAgencyError`, `03` 은 빈 결과.
    """
    if not isinstance(js, dict):
        raise DatagoFormatError(dataset_id, 200, "JSON 최상위가 객체가 아니다")
    resp: Any = js.get("response")
    if not isinstance(resp, dict):
        raise DatagoFormatError(dataset_id, 200, "응답 봉투(response)가 없다")
    head: Any = resp.get("header") or {}
    code = str(head.get("resultCode", "") if isinstance(head, dict) else "").strip()
    msg = str(head.get("resultMsg", "") if isinstance(head, dict) else "").strip()
    if code in _AGENCY_NODATA:
        return [], 0
    if code and code not in _AGENCY_OK:
        raise DatagoAgencyError(dataset_id, code, msg)
    body: Any = resp.get("body")
    if not isinstance(body, dict):
        raise DatagoFormatError(dataset_id, 200, "응답 본문(body)이 없다 — 키·트래픽 확인")
    items_box: Any = body.get("items")
    raw_items: Any = items_box.get("item") if isinstance(items_box, dict) else None
    if isinstance(raw_items, dict):
        raw_items = [raw_items]
    if raw_items in (None, ""):
        raw_items = []
    if not isinstance(raw_items, list):
        raise DatagoFormatError(dataset_id, 200, "items.item 이 목록이 아니다")
    items: list[dict[str, Any]] = []
    for x in raw_items:
        if not isinstance(x, dict):
            raise DatagoFormatError(dataset_id, 200, "item 이 객체가 아니다")
        items.append({str(k): v for k, v in x.items()})
    return items, _int_or(body.get("totalCount"), 0)


def unwrap_xml_items(
    root: Element, dataset_id: str = ""
) -> tuple[list[dict[str, str]], int | None]:
    """포털 공통 XML 봉투(`response/header/resultCode`·`response/body/items/item`)를 벗긴다.

    각 `item` 의 자식 태그 → 문자열(앞뒤 공백 그대로 — 관세청 금액 `" 13,886,115"` 은 모델이 푼다).
    `totalCount` 가 없으면 None. 기관 오류는 `DatagoAgencyError`, `03` 은 빈 결과.
    """
    if root.tag == _ENVELOPE:
        raise DatagoFormatError(dataset_id, 200, "게이트웨이 오류 봉투가 기관 응답으로 왔다")
    code = (root.findtext("header/resultCode") or "").strip()
    msg = (root.findtext("header/resultMsg") or "").strip()
    if code in _AGENCY_NODATA:
        return [], 0
    if code and code not in _AGENCY_OK:
        raise DatagoAgencyError(dataset_id, code, msg)
    body = root.find("body")
    if body is None:
        raise DatagoFormatError(dataset_id, 200, "응답 본문(body)이 없다")
    items = [
        {child.tag: child.text or "" for child in item} for item in body.iterfind("items/item")
    ]
    total = body.findtext("totalCount")
    return items, (_int_or(total, None) if total is not None else None)


def _int_or[T](v: object, default: T) -> int | T:
    try:
        return int(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


_MISSING_NUMBER: Final = frozenset({"", "-"})


def parse_amount(raw: object) -> Decimal | None:
    """포털 숫자 문자열 → `Decimal`. 쉼표·앞뒤 공백을 턴다(관세청 10일 잠정치 `" 13,886,115"`).

    빈 값·`-` 는 None(값 없음 — 0 으로 채우지 않는다, ET D-019). 그 밖에 숫자가 아니면
    `ValueError`(형식이 바뀐 것 — 삼키지 않는다).
    """
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if s in _MISSING_NUMBER:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"숫자가 아니다: {str(raw)[:40]!r}") from None
    if not d.is_finite():
        raise ValueError(f"숫자가 아니다: {str(raw)[:40]!r}")
    return d


def _no_scrub(msg: str) -> str:
    return msg


@dataclass(frozen=True)
class DatagoResponse:
    """받은 응답 하나(게이트웨이 오류는 이미 걸렀다). 본문은 `json()`·`xml()`·`items()`."""

    dataset_id: str
    status: int
    content: bytes = field(repr=False)  # 본문은 repr 에 싣지 않는다(크기·기관이 되돌려 보낸 문구)
    want: Want
    key_form: str  # 통한 키 형태의 이름(`디코딩`·`그대로`) — 값이 아니다
    received_at: datetime
    _cache: dict[str, Any] = field(default_factory=dict[str, Any], repr=False, compare=False)
    # 보낸 전송층의 키 지우기(아는 키의 모든 형태). 기관 오류 문구가 키를 되돌려 보내면 길이와
    # 상관없이 지운다 — `_safe_agency_message` 의 긴 토큰 가리기는 짧은 키를 못 잡는다(절대 규칙 5)
    _scrub: Callable[[str], str] = field(default=_no_scrub, repr=False, compare=False)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", "replace")

    def json(self) -> Any:
        if "json" not in self._cache:
            try:
                self._cache["json"] = json.loads(self.content)
            except ValueError:
                raise DatagoFormatError(self.dataset_id, self.status, "JSON 이 아니다") from None
        return self._cache["json"]

    def xml(self) -> Element:
        if "xml" not in self._cache:
            try:
                self._cache["xml"] = _xml_fromstring(self.content)
            except Exception as e:  # defusedxml 의 금지 구성·문법 오류 — 사유를 담아 올린다
                raise DatagoFormatError(
                    self.dataset_id, self.status, f"XML 이 아니다({type(e).__name__})"
                ) from None
        root: Element = self._cache["xml"]
        return root

    def items(self) -> tuple[list[dict[str, Any]], int | None]:
        """`want` 에 맞춰 봉투를 벗긴 행과 `totalCount`."""
        try:
            if self.want == "xml":
                rows, total = unwrap_xml_items(self.xml(), self.dataset_id)
                return [dict(r) for r in rows], total
            return unwrap_items(self.json(), self.dataset_id)
        except DatagoAgencyError as e:  # 같은 오류를 키를 지운 문구로 다시 올린다
            raise DatagoAgencyError(
                self.dataset_id, e.code, self._scrub(e.agency_message)
            ) from None


class DatagoTransport:
    """공공데이터포털 호출 하나의 길 — 키 형태·리미터·예산·오류 처리.

    `limiters`·`budgets` 는 포털 데이터셋 ID(`"15100475"`)를 키로 받는다. 등록하지 않은 데이터셋을
    부르면 `KeyError` — 한도 없이 조용히 부르지 않는다.
    """

    def __init__(
        self,
        service_key: SecretStr,
        *,
        limiters: Mapping[str, RateLimiter],
        budgets: Mapping[str, DailyBudget],
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        retry_waits_s: tuple[float, ...] = RETRY_WAITS_S,
        priority: Priority = Priority.P3,
        acquire_timeout: float | None = 120.0,
        max_bytes: int = MAX_BYTES,
        now: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        raw = service_key.get_secret_value()
        self._forms = key_forms(raw)
        self._secrets: list[str] = sorted(
            {v for _, k in self._forms for v in (k, quote(k, safe=""), quote_plus(k))},
            key=len,
            reverse=True,
        )
        for ds in (*limiters, *budgets):
            _check_dataset_id(ds)
        self._limiters = dict(limiters)
        self._budgets = dict(budgets)
        self._timeout = timeout
        self._retry_waits = retry_waits_s
        self._priority = priority
        self._acquire_timeout = acquire_timeout
        self._max_bytes = max_bytes
        self._now = now
        self._sleep = sleep
        self._monotonic = monotonic
        self._http = make_client(_BASE, connect_s=CONNECT_S, read_s=timeout, transport=transport)
        self.key_form: str | None = None  # 통한 형태 이름(값이 아니다)
        self.calls = 0  # 실제로 보낸 HTTP 요청 수(시험·운영 화면용)

    def __repr__(self) -> str:
        return f"DatagoTransport(datasets={sorted(self._limiters)}, key=***)"

    # ── 만들기 ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def for_datasets(
        cls,
        service_key: SecretStr,
        redis: Redis,
        dataset_ids: Iterable[str],
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> DatagoTransport:
        """데이터셋마다 Redis 리미터(`rl:datago:<해시>`)·일 예산(`budget:datago:<ID>:<날짜>`).

        값은 config/limits.yaml 의 `datago`(rate·daily_cap_per_dataset). 다른 프로세스가 같은
        데이터셋을 불러도 같은 버킷·같은 카운터를 쓴다.
        """
        src = (limits or load_limits()).source(LIMITS_SOURCE)
        cap = src.daily_cap_per_dataset
        if cap is None:
            raise ValueError("limits.yaml datago.daily_cap_per_dataset 가 없다")
        cfg = src.rate_config()
        ids = [_check_dataset_id(d) for d in dataset_ids]
        limiters: dict[str, RateLimiter] = {
            d: RedisRateLimiter.scoped(redis, LIMITS_SOURCE, d, cfg, clock) for d in ids
        }
        budgets = {d: DailyBudget(redis, LIMITS_SOURCE, cap, scope=d) for d in ids}
        return cls(service_key, limiters=limiters, budgets=budgets, **kw)

    def close(self) -> None:
        self._http.close()

    def datasets(self) -> tuple[str, ...]:
        return tuple(sorted(self._limiters))

    # ── 부르기 ──────────────────────────────────────────────────────────────────────────

    def call(
        self, dataset_id: str, path: str, params: Mapping[str, Any], *, want: Want
    ) -> DatagoResponse:
        """`path`(예: `/1220000/Itemtrade/getItemtradeList`)를 부른다. `serviceKey` 는 여기서.

        예외: `BudgetExhausted`(그날 상한·닫힘 — 부르지 않았다), `RateLimitTimeout`(허가 대기 초과),
        `DatagoError` 갈래(머리말).
        """
        limiter = self._limiters.get(dataset_id)
        budget = self._budgets.get(dataset_id)
        if limiter is None or budget is None:
            raise KeyError(f"데이터셋 {dataset_id} 의 리미터·예산이 없다 — for_datasets 에 넣는다")
        if not path.startswith("/") or "://" in path or "?" in path:
            raise ValueError(f"path 는 '/' 로 시작하는 경로만: {path!r}")
        if KEY_PARAM in params:
            raise ValueError("serviceKey 는 전송층이 넣는다 — params 에 넣지 않는다")
        forms = self._ordered_forms()
        attempt = 0
        form_i = 0
        while True:
            label, key = forms[form_i]
            status, content, gw, transport_err = self._send(
                limiter, budget, dataset_id, path, {**params, KEY_PARAM: key}
            )
            if transport_err is not None:
                if attempt < len(self._retry_waits):
                    self._sleep(self._retry_waits[attempt])
                    attempt += 1
                    continue
                raise DatagoError(dataset_id, None, transport_err)
            action = gw.action if gw is not None else None
            key_failed = action is GwAction.KEY or (gw is None and status == 401)
            if key_failed:
                if form_i + 1 < len(forms):
                    form_i += 1
                    continue
                raise DatagoKeyError(
                    dataset_id,
                    status,
                    self._reason(status, content, gw)
                    + f" — 키 형태 {', '.join(n for n, _ in forms)} 를 모두 시도했다",
                    gw,
                )
            throttled = action is GwAction.THROTTLE or (gw is None and status == 429)
            if throttled:
                limiter.on_rate_limited()
                if attempt < len(self._retry_waits):
                    self._sleep(self._retry_waits[attempt])
                    attempt += 1
                    continue
                raise DatagoThrottled(dataset_id, status, self._reason(status, content, gw), gw)
            if action is GwAction.QUOTA:
                reason = self._reason(status, content, gw)
                budget.exhaust(self._now(), reason)
                raise DatagoQuotaExceeded(dataset_id, status, reason, gw)
            if action is GwAction.GONE:
                raise DatagoGone(dataset_id, status, self._reason(status, content, gw), gw)
            if action is GwAction.DENIED:
                raise DatagoAccessDenied(dataset_id, status, self._reason(status, content, gw), gw)
            retry = action is GwAction.RETRY or (gw is None and status >= 500)
            if retry and attempt < len(self._retry_waits):
                self._sleep(self._retry_waits[attempt])
                attempt += 1
                continue
            if gw is not None or status != 200:
                err = DatagoError(dataset_id, status, self._reason(status, content, gw), gw)
                err.retryable = retry
                raise err
            self.key_form = label
            return DatagoResponse(
                dataset_id, status, content, want, label, self._now(), _scrub=self._scrub
            )

    def _ordered_forms(self) -> list[tuple[str, str]]:
        if self.key_form is None:
            return list(self._forms)
        first = [f for f in self._forms if f[0] == self.key_form]
        return first + [f for f in self._forms if f[0] != self.key_form]

    def _send(
        self,
        limiter: RateLimiter,
        budget: DailyBudget,
        dataset_id: str,
        path: str,
        params: dict[str, Any],
    ) -> tuple[int, bytes, GwError | None, str | None]:
        """한 번 보낸다 — (상태, 본문, 게이트웨이 오류, 전송 오류 사유)."""
        limiter.acquire(self._priority, dataset_id, self._acquire_timeout)
        budget.take(self._now())  # 상한·닫힘이면 BudgetExhausted — 부르지 않는다
        self.calls += 1
        try:
            status, content = get_capped(
                self._http,
                path,
                params=params,
                max_bytes=self._max_bytes,
                total_s=self._timeout * 3,
                clock=self._monotonic,
                source=SOURCE,
                dataset=dataset_id,
            )
        except FetchError as e:  # 크기·전체 시간 초과 — 다시 불러도 같을 가능성이 크다
            raise DatagoError(dataset_id, e.status, self._scrub(e.reason)) from None
        except httpx.HTTPError as e:
            return 0, b"", None, self._scrub(f"{type(e).__name__}: {e}", params)
        text = content[:BODY_SCAN].decode("utf-8", "replace")
        return status, content, parse_gw_error(text), None

    def _reason(self, status: int, content: bytes, gw: GwError | None) -> str:
        if gw is not None:
            tail = f" {gw.message}" if gw.message else ""
            return self._scrub(f"HTTP {status} · {gw.described}{tail}")
        return self._scrub(f"HTTP {status} · {why(_Body(status, content)) or '본문 없음'}")

    def _scrub(self, msg: str, params: Mapping[str, Any] | None = None) -> str:
        return scrub(msg, params, self._secrets)


class _Body:
    """`kbj.data.http.why` 가 읽는 응답 모양(status_code·text·json)."""

    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self._content = content

    @property
    def text(self) -> str:
        return self._content[:BODY_SCAN].decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self._content)


def _check_dataset_id(dataset_id: str) -> str:
    if not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError(f"포털 데이터셋 ID 는 숫자여야 한다: {dataset_id!r}")
    return dataset_id
