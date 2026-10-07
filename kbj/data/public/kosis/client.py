"""통계청(국가데이터처) KOSIS 공유서비스 — 공개 등급(국내 통계, DATA_TIERS §1).

신규(원본 수집 코드 없음 — ET `monitor/bok/audit.js` 에 키 이름만). 명세 `docs/probe_results.md` §5.

- **HTTPS 만**(2026-03-05 공지). 주소 상수는 이 폴더에만 둔다(scripts/check_canonical.py `kosis`).
- 통계자료(통계표 선택) `Param/statisticsParameterData.do?method=getList` — `orgId`·`tblId`·
  `objL1`(분류1, 필수)·`itmId`·`prdSe` + (`startPrdDe`·`endPrdDe`) 또는 `newEstPrdCnt`.
  `format=json`·`jsonVD=Y`(표준 JSON). 통계표 설명 `statisticsData.do?method=getMeta&type=TBL`.
- 한도: **분당 200건**(2026-07-15 공지) → limits.yaml `kosis` 3/s(= 180/분). 일 한도 미공표
  [실측 필요].
- 오류 봉투 `{"err": "<코드>", "errMsg": "…"}` — 코드표는 개발 가이드 기준 [추정 — 실측 필요]:
  `30` 조회 결과 없음 → 빈 목록, `10`·`11` 키 → `KosisKeyError`(critical), `40`·`41`·`42` 호출 한도
  → 리미터 감속 + `KosisThrottled`, 그 밖 → `KosisError`.
- 값(`DT`)은 문자열. 숫자 → `Decimal`, 통계부호·빈 값(`-`·`x`·`…` 등) → None(`quality=invalid`,
  원문은 `raw_value` 에 남긴다). 국제통계 표는 로그인 [제안](probe_results §5.3) — 여기서는 받지
  않는다(데이터셋 목록에 국내 표만).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import quote, quote_plus

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.core.time import utcnow
from kbj.data.http import FetchError, get_capped, make_client, scrub
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "KOSIS"
LIMITS_SOURCE: Final = "kosis"
_BASE: Final = "https://kosis.kr/openapi"
_PARAM_DATA: Final = "/Param/statisticsParameterData.do"
_META: Final = "/statisticsData.do"

CONNECT_S: Final = 5.0
TIMEOUT_S: Final = 30.0
MAX_BYTES: Final = 32 * 1024 * 1024
PRD_SE: Final = frozenset({"D", "M", "Q", "H", "S", "Y", "F", "IR"})

# 오류 코드 [추정 — 개발 가이드 오류 표, 실측 필요]
_NO_DATA: Final = frozenset({"30"})
_KEY: Final = frozenset({"10", "11"})
_LIMITED: Final = frozenset({"40", "41", "42"})


class KosisError(FetchError):
    """KOSIS 호출 실패. `code` 는 응답 `err`(전송 오류면 빈 문자열)."""

    critical: bool = False

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        self.code = code
        super().__init__(SOURCE, "", status, f"err={code} {message}".strip() if code else message)


class KosisKeyError(KosisError):
    """키 누락·만료 — 운영 critical."""

    critical = True


class KosisThrottled(KosisError):
    """호출 한도 — 감속했다. 다음 주기에 다시."""


class KosisRow(BaseModel):
    """통계자료 한 행. `source` = `KOSIS:<tblId>`, 기준 시점은 `prd_de`(주기 `prd_se` 형식)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    org_id: str
    tbl_id: str
    tbl_nm: str
    itm_id: str
    itm_nm: str
    unit: str
    prd_se: str
    prd_de: str
    classes: tuple[tuple[str, str], ...]  # (C1 코드, C1 이름) … C8 까지 있는 것만
    value: Decimal | None
    raw_value: str
    last_changed: str  # LST_CHN_DE

    @property
    def as_of(self) -> str:
        """값이 가리키는 시점(`PRD_DE` — 주기 `prd_se` 형식, 받은 시각이 아니다)."""
        return self.prd_de

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.value is not None else Quality.INVALID


def parse_dt(raw: object) -> Decimal | None:
    """`DT` → Decimal. 숫자가 아니면(통계부호·빈 값) None — 원문은 행이 따로 갖는다."""
    s = "" if raw is None else str(raw).replace(",", "").strip()
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _s(row: Mapping[str, Any], key: str) -> str:
    v = row.get(key)
    return "" if v is None else str(v).strip()


class KosisClient:
    """KOSIS 통계자료·통계표 설명."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        priority: Priority = Priority.P3,
        acquire_timeout: float | None = 120.0,
        now: Callable[[], datetime] = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        key = api_key.get_secret_value().strip()
        if not key:
            raise KosisKeyError("", "KOSIS 키가 비었다")
        self._key = key
        self._secrets = [key, quote(key, safe=""), quote_plus(key)]
        self._limiter = limiter
        self._timeout = timeout
        self._priority = priority
        self._acquire_timeout = acquire_timeout
        self._now = now
        self._monotonic = monotonic
        self._http = make_client(_BASE, connect_s=CONNECT_S, read_s=timeout, transport=transport)
        self.calls = 0

    def __repr__(self) -> str:
        return "KosisClient(key=***)"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> KosisClient:
        """`KBJ_KOSIS_KEY` + Redis 리미터 `rl:kosis:<키 해시>`(limits.yaml `kosis`)."""
        if settings.kosis_key is None:
            raise KosisKeyError("", "KBJ_KOSIS_KEY 가 없다")
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(
            redis, LIMITS_SOURCE, settings.kosis_key.get_secret_value(), src.rate_config(), clock
        )
        return cls(settings.kosis_key, limiter=limiter, **kw)

    def close(self) -> None:
        self._http.close()

    def parameter_data(
        self,
        org_id: str,
        tbl_id: str,
        obj_l1: str,
        itm_id: str,
        prd_se: str,
        *,
        start: str | None = None,
        end: str | None = None,
        newest: int | None = None,
        obj_more: Sequence[str] = (),
    ) -> list[KosisRow]:
        """통계표 선택 방식 통계자료. 기간은 (start, end) 또는 newest(최근 N개) 중 하나.

        둘 다 없으면 KOSIS 기본(최근 1개). `obj_more` 는 objL2~objL8.
        """
        if prd_se not in PRD_SE:
            raise ValueError(f"prdSe 는 {', '.join(sorted(PRD_SE))} 중 하나: {prd_se!r}")
        for name, v in (("orgId", org_id), ("tblId", tbl_id), ("objL1", obj_l1), ("itmId", itm_id)):
            if not v or not v.strip():
                raise ValueError(f"{name} 는 비울 수 없다")
        if len(obj_more) > 7:
            raise ValueError("분류는 objL1~objL8 까지")
        params: dict[str, Any] = {
            "method": "getList",
            "orgId": org_id,
            "tblId": tbl_id,
            "objL1": obj_l1,
            "itmId": itm_id,
            "prdSe": prd_se,
            "format": "json",
            "jsonVD": "Y",
        }
        for i, obj in enumerate(obj_more, start=2):
            params[f"objL{i}"] = obj
        if newest is not None:
            if start is not None or end is not None:
                raise ValueError("기간(start·end)과 newest 는 함께 쓰지 않는다")
            if newest < 1:
                raise ValueError("newest 는 1 이상")
            params["newEstPrdCnt"] = newest
        elif start is not None or end is not None:
            if start is None or end is None or start > end:
                raise ValueError("start·end 는 둘 다, start <= end 로")
            params["startPrdDe"] = start
            params["endPrdDe"] = end
        rows = self._get(_PARAM_DATA, params)
        return [self._row(r) for r in rows]

    def meta(self, org_id: str, tbl_id: str) -> list[dict[str, str]]:
        """통계표 설명(메타) — 행 그대로(문자열)."""
        params = {
            "method": "getMeta",
            "type": "TBL",
            "orgId": org_id,
            "tblId": tbl_id,
            "format": "json",
            "jsonVD": "Y",
        }
        return [
            {k: "" if v is None else str(v) for k, v in r.items()} for r in self._get(_META, params)
        ]

    # ── 공통 ────────────────────────────────────────────────────────────────────────────

    def _get(self, path: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        sent = {**params, "apiKey": self._key}
        self._limiter.acquire(self._priority, f"kosis:{path}", self._acquire_timeout)
        self.calls += 1
        try:
            status, content = get_capped(
                self._http,
                path,
                params=sent,
                max_bytes=MAX_BYTES,
                total_s=self._timeout * 2,
                clock=self._monotonic,
                source=SOURCE,
                dataset=str(params.get("tblId", "")),
            )
        except FetchError as e:
            raise KosisError("", self._scrub(e.reason, sent), status=e.status) from None
        except httpx.HTTPError as e:
            raise KosisError("", self._scrub(f"{type(e).__name__}: {e}", sent)) from None
        try:
            js: Any = json.loads(content)
        except ValueError:
            head = " ".join(content[:200].decode("utf-8", "replace").split())
            raise KosisError(
                "", self._scrub(f"HTTP {status} JSON 이 아니다: {head}", sent)
            ) from None
        if isinstance(js, dict) and "err" in js:
            code = str(js.get("err", "")).strip()
            msg = self._scrub(str(js.get("errMsg", "")).strip(), sent)
            if code in _NO_DATA:
                return []
            if code in _KEY:
                raise KosisKeyError(code, msg, status=status)
            if code in _LIMITED:
                self._limiter.on_rate_limited()
                raise KosisThrottled(code, msg, status=status)
            raise KosisError(code, msg, status=status)
        if status != 200:
            raise KosisError("", f"HTTP {status}", status=status)
        if not isinstance(js, list):
            raise KosisError("", "응답이 목록이 아니다", status=status)
        out: list[dict[str, Any]] = []
        for r in js:  # 객체가 아닌 행을 조용히 건너뛰지 않는다 — 형식이 바뀐 것이다
            if not isinstance(r, dict):
                raise KosisError("", "응답 목록에 객체가 아닌 행이 있다", status=status)
            out.append({str(k): v for k, v in r.items()})
        return out

    @staticmethod
    def _row(r: Mapping[str, Any]) -> KosisRow:
        tbl = _s(r, "TBL_ID")
        classes = tuple((_s(r, f"C{i}"), _s(r, f"C{i}_NM")) for i in range(1, 9) if _s(r, f"C{i}"))
        raw = _s(r, "DT")
        return KosisRow(
            source=f"{SOURCE}:{tbl}",
            org_id=_s(r, "ORG_ID"),
            tbl_id=tbl,
            tbl_nm=_s(r, "TBL_NM"),
            itm_id=_s(r, "ITM_ID"),
            itm_nm=_s(r, "ITM_NM"),
            unit=_s(r, "UNIT_NM"),
            prd_se=_s(r, "PRD_SE"),
            prd_de=_s(r, "PRD_DE"),
            classes=classes,
            value=parse_dt(raw),
            raw_value=raw,
            last_changed=_s(r, "LST_CHN_DE"),
        )

    def _scrub(self, msg: str, params: Mapping[str, Any] | None = None) -> str:
        return scrub(msg, params, self._secrets)
