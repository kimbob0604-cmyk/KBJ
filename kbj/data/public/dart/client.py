"""OpenDART 클라이언트 — 공개 등급(DATA_TIERS §1: 재배포 금지 조항 없음, 출처 "DART").

승격 원본: ETF-Traker `dart-report/dartreport/client.py`(`BASE`:21, `REPRT`:24, `EMPTY_STATUSES`:32,
`DartError`:35, `DartClient`:43 — `_cache_path`:66, `_wait`:73, `_get`:79, `get_json`:89,
`get_zip`:107, `corp_code`:129, `financials`:151, `disclosures`:169, `document_texts`:196) +
`board/ingest/dart.py`(`STATUS_KO`:37, `_decode_status`:55, `020` 일 한도).

dart-report 에서 바꾼 것
- 키: `SecretStr` 로 받고(문자열도 받는다) 없으면 `Settings().dart_api_key`. 키가 없으면
  `SystemExit` 대신 `DartKeyMissing`. 길이 40자 검사는 경고 로그(길이 값은 남기지 않는다).
- 스로틀: 0.12초 `_wait` 대신 공용 리미터(`rl:dart:<키 해시>` 8/s — limits.yaml `dart`). 리미터를 안
  주면 같은 값의 프로세스 안 리미터.
- 일 예산: `budget:dart:<KST 날짜>`(18,000 = 20,000 의 90%). `020`(요청 제한 초과)을 받으면
  그날 닫고 `DartQuotaExceeded`(ET `board/ingest/dart.py:43`).
- 캐시: `cache_dir` 를 줄 때만(기본 없음). 캐시 키(파일 이름·내용)에 `crtfc_key` 가 없다. 공시 목록
  (`list.json`)은 바뀌는 값이라 캐시하지 않는다.
- HTTP: httpx(넘겨주기 안 따라감), 크기 상한·전체 시간 상한, 전송 오류·5xx 재시도
  (ET `http.get` 방식 — 기본 3회, 0.8초씩 늘림). 실패 문구에는 키가 없다(절대 규칙 5).
- zip 응답(corpCode·document)은 압축을 풀기 전에 크기를 본다.

주소(`https://opendart.fss.or.kr/api`)는 이 폴더에만 둔다(scripts/check_canonical.py 그룹 `dart`).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import zipfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import httpx
from pydantic import SecretStr
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.time import utcnow
from kbj.data.budget import DailyBudget
from kbj.data.http import FetchError, get_capped, make_client, scrub
from kbj.data.limits import Limits, load_limits
from kbj.data.public.dart.corp_code import CorpCode, listed_map, parse_corp_codes
from kbj.data.ratelimit import Clock, LocalRateLimiter, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "DART"
LIMITS_SOURCE: Final = "dart"
_BASE: Final = "https://opendart.fss.or.kr/api"
KEY_PARAM: Final = "crtfc_key"
KEY_LEN: Final = 40

# 보고서 코드 → (분기 라벨, 누적 개월 수)
REPRT: Final[Mapping[str, tuple[str, int]]] = {
    "11013": ("1Q", 3),
    "11012": ("2Q", 6),
    "11014": ("3Q", 9),
    "11011": ("4Q", 12),
}
# "정상적인 빈 결과"로 보는 상태 코드
EMPTY_STATUSES: Final = frozenset({"013"})
QUOTA_STATUS: Final = "020"
KEY_STATUSES: Final = frozenset({"010", "011", "012", "901"})

# DART 는 실패를 상태 코드로 준다. 코드만 보면 무슨 일인지 알 수 없어서 풀어 준다(ET STATUS_KO)
STATUS_KO: Final[Mapping[str, str]] = {
    "010": "등록되지 않은 인증키",
    "011": "사용할 수 없는 인증키 (일시 중지)",
    "012": "접근할 수 없는 IP",
    "013": "조회된 데이터가 없음",
    "014": "파일이 존재하지 않음",
    "020": "요청 제한 초과 (일 20,000건)",
    "021": "조회 가능한 회사 개수 초과",
    "100": "필드의 부적절한 값",
    "101": "부적절한 접근",
    "800": "시스템 점검 중 — 인증키 문제가 아니다. 시간을 두고 다시",
    "900": "정의되지 않은 오류",
    "901": "사용 유의사항 위반에 따른 이용제한",
}
_STATUS_RE = re.compile(r"<status>\s*(\d+)\s*</status>")
_MESSAGE_RE = re.compile(r"<message>([^<]*)</message>")

CONNECT_S: Final = 5.0
TIMEOUT_S: Final = 30.0
MAX_BYTES: Final = 64 * 1024 * 1024  # corpCode.xml zip 은 수 MB, 원문 zip 은 더 클 수 있다

log = logging.getLogger(__name__)


class DartError(FetchError):
    """DART 실패. `code` = DART 상태 코드(`"020"` — 전송 오류면 빈 문자열).

    문구는 `status=<코드> <뜻> <메시지>`(legacy 가 이 꼴을 정규식으로 읽는다 — ET triggers.py).
    """

    def __init__(
        self, code: str, message: str, endpoint: str, *, http_status: int | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.endpoint = endpoint
        known = STATUS_KO.get(code, "")
        reason = f"status={code} {known} {message}" if code else message
        super().__init__(SOURCE, endpoint, http_status, " ".join(reason.split()))

    @property
    def critical(self) -> bool:
        """키·IP·이용제한 — 운영 critical."""
        return self.code in KEY_STATUSES


class DartQuotaExceeded(DartError):
    """`020` 요청 제한 초과 — 그날 예산을 닫았다."""


class DartKeyMissing(DartError):
    """키가 없다(`KBJ_DART_API_KEY`)."""

    @property
    def critical(self) -> bool:
        return True


def decode_status(text: str) -> tuple[str, str] | None:
    """XML 오류 응답(`<result><status>…</status><message>…</message>`) → (코드, 메시지).

    상태 코드를 못 찾으면 None. 메시지는 80자까지(ET `_decode_status`).
    """
    head = (text or "")[:4000]
    st = _STATUS_RE.search(head)
    if st is None:
        return None
    msg = _MESSAGE_RE.search(head)
    return st.group(1), (msg.group(1).strip()[:80] if msg else "")


def describe_status(text: str) -> str:
    """XML 오류 응답을 사람이 읽는 한 줄로(ET `_decode_status` 문구)."""
    hit = decode_status(text)
    if hit is None:
        return f"응답을 해석하지 못했다: {' '.join((text or '')[:120].split())}"
    code, msg = hit
    tail = f" ({msg})" if msg else ""
    return f"status={code} {STATUS_KO.get(code, '알 수 없는 코드')}{tail}"


class DartClient:
    """OpenDART. 메서드는 dart-report 그대로 + `get_raw`·`corp_codes`·`corp_map`."""

    def __init__(
        self,
        api_key: SecretStr | str | None = None,
        *,
        cache_dir: Path | None = None,
        limiter: RateLimiter | None = None,
        budget: DailyBudget | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        retries: int = 3,
        backoff_s: float = 0.8,
        priority: Priority = Priority.P3,
        acquire_timeout: float | None = 120.0,
        max_bytes: int = MAX_BYTES,
        now: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if api_key is None:
            api_key = Settings().dart_api_key
        key = (
            api_key.get_secret_value() if isinstance(api_key, SecretStr) else api_key or ""
        ).strip()
        if not key:
            raise DartKeyMissing("", "KBJ_DART_API_KEY 가 없다", "-")
        if len(key) != KEY_LEN:
            log.warning("DART 인증키 길이가 40자가 아니다 — 오타 확인")
        if retries < 1:
            raise ValueError("retries 는 1 이상(첫 시도 포함)")
        self._key = key
        self._secrets = [key]
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._limiter: RateLimiter = limiter or LocalRateLimiter(
            load_limits().source(LIMITS_SOURCE).rate_config()
        )
        self._budget = budget
        self.timeout = timeout
        self._retries = retries
        self._backoff = backoff_s
        self._priority = priority
        self._acquire_timeout = acquire_timeout
        self._max_bytes = max_bytes
        self._now = now
        self._sleep = sleep
        self._monotonic = monotonic
        self._http = make_client(_BASE, connect_s=CONNECT_S, read_s=timeout, transport=transport)
        self._corps: list[CorpCode] | None = None
        self.calls = 0

    def __repr__(self) -> str:
        return f"DartClient(key=***, cache_dir={self.cache_dir!s})"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> DartClient:
        """`KBJ_DART_API_KEY` + Redis 리미터 `rl:dart:<키 해시>` + 일 예산 `budget:dart:<날짜>`."""
        if settings.dart_api_key is None:
            raise DartKeyMissing("", "KBJ_DART_API_KEY 가 없다", "-")
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        if src.daily_cap is None:
            raise ValueError("limits.yaml dart.daily_cap 가 없다")
        key = settings.dart_api_key.get_secret_value()
        limiter = RedisRateLimiter.scoped(redis, LIMITS_SOURCE, key, src.rate_config(), clock)
        budget = DailyBudget(redis, LIMITS_SOURCE, src.daily_cap)
        return cls(settings.dart_api_key, limiter=limiter, budget=budget, **kw)

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ 내부

    def _cache_path(self, endpoint: str, params: Mapping[str, Any], ext: str) -> Path | None:
        """캐시 파일. 키(`crtfc_key`)는 이름·내용 어디에도 넣지 않는다."""
        if self.cache_dir is None:
            return None
        keyed = {k: v for k, v in sorted(params.items()) if k != KEY_PARAM}
        digest = hashlib.sha256(
            f"{endpoint}|{json.dumps(keyed, ensure_ascii=False, default=str)}".encode()
        ).hexdigest()[:16]
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", endpoint)
        return self.cache_dir / f"{safe}_{digest}.{ext}"

    def _request(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> bytes:
        """한 엔드포인트를 부른다(재시도 포함). 200 이 아니면 `DartError`."""
        if KEY_PARAM in params:
            raise ValueError("crtfc_key 는 클라이언트가 넣는다 — params 에 넣지 않는다")
        if "/" in endpoint or "?" in endpoint:
            raise ValueError(f"엔드포인트 이름만: {endpoint!r}")
        sent = {**params, KEY_PARAM: self._key}
        attempts = self._retries if retries is None else max(1, retries)
        total_s = timeout if timeout is not None else self.timeout
        last = ""
        status: int | None = None
        for i in range(attempts):
            self._limiter.acquire(self._priority, f"dart:{endpoint}", self._acquire_timeout)
            if self._budget is not None:
                self._budget.take(self._now())  # 상한·닫힘이면 BudgetExhausted — 부르지 않는다
            self.calls += 1
            try:
                status, content = get_capped(
                    self._http,
                    f"/{endpoint}",
                    params=sent,
                    max_bytes=self._max_bytes,
                    total_s=total_s,
                    clock=self._monotonic,
                    source=SOURCE,
                    dataset=endpoint,
                )
            except FetchError as e:  # 크기·시간 상한 — 다시 불러도 같을 가능성이 크다
                raise DartError("", self._scrub(e.reason, sent), endpoint) from None
            except httpx.HTTPError as e:
                last = self._scrub(f"{type(e).__name__}: {e}", sent)
            else:
                if status == 200:
                    return content
                head = content[:2000].decode("utf-8", "replace")
                last = self._scrub(f"HTTP {status} · {describe_status(head)}", sent)
                if status < 500:
                    raise DartError("", last, endpoint, http_status=status)
            if i < attempts - 1:
                self._sleep(self._backoff * (i + 1))
        raise DartError("", f"{attempts}회 실패: {last}", endpoint, http_status=status)

    def _scrub(self, msg: str, params: Mapping[str, Any] | None = None) -> str:
        return scrub(msg, params, self._secrets)

    def _status_error(self, code: str, message: str, endpoint: str) -> DartError:
        if code == QUOTA_STATUS:
            reason = f"status={code} {STATUS_KO[code]} {message}".strip()
            if self._budget is not None:
                self._budget.exhaust(self._now(), reason)
            return DartQuotaExceeded(code, message, endpoint)
        return DartError(code, message, endpoint)

    # ------------------------------------------------------------------ 공개 API(dart-report)

    def get_json(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        use_cache: bool = True,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        """JSON 엔드포인트. 빈 결과(013)는 예외 대신 `list=[]` 로, 그 밖의 오류 상태는 `DartError`.

        `020` 이면 그날 예산을 닫고 `DartQuotaExceeded`.
        """
        cache = self._cache_path(endpoint, params, "json") if use_cache else None
        if cache is not None and cache.exists():
            try:
                cached: Any = json.loads(cache.read_text(encoding="utf-8"))
            except ValueError:
                log.warning("DART 캐시 파일이 깨졌다 — 다시 받는다: %s", cache.name)
            else:
                if isinstance(cached, dict):
                    return {str(k): v for k, v in cached.items()}
        body = self._request(endpoint, params, timeout=timeout, retries=retries)
        try:
            data: Any = json.loads(body)
        except ValueError:
            hit = decode_status(body[:4000].decode("utf-8", "replace"))
            if hit is not None:
                raise self._status_error(hit[0], hit[1], endpoint) from None
            raise DartError("", "JSON 이 아니다", endpoint, http_status=200) from None
        if not isinstance(data, dict):
            raise DartError("", "JSON 최상위가 객체가 아니다", endpoint, http_status=200)
        out: dict[str, Any] = {str(k): v for k, v in data.items()}
        status = str(out.get("status", ""))
        message = self._scrub(str(out.get("message", "")))
        if status in EMPTY_STATUSES:
            out = {"status": status, "message": message, "list": []}
        elif status != "000":
            raise self._status_error(status, message, endpoint)
        if cache is not None:
            cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out

    def get_raw(
        self,
        endpoint: str,
        params: Mapping[str, Any],
        *,
        use_cache: bool = False,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> bytes:
        """zip 을 주는 엔드포인트(corpCode.xml·document.xml)의 바이트. zip 이 아니면 본문의 상태
        코드로 `DartError`(`013` 포함 — `get_zip` 은 013 을 None 으로 바꾼다)."""
        cache = self._cache_path(endpoint, params, "zip") if use_cache else None
        if cache is not None and cache.exists():
            data = cache.read_bytes()
            if data.startswith(b"PK"):
                return data
        body = self._request(endpoint, params, timeout=timeout, retries=retries)
        if not body.startswith(b"PK"):
            hit = decode_status(body[:4000].decode("utf-8", "replace"))
            if hit is not None:
                raise self._status_error(hit[0], self._scrub(hit[1]), endpoint)
            raise DartError("", "zip 도 상태 응답도 아니다", endpoint, http_status=200)
        if cache is not None:
            cache.write_bytes(body)
        return body

    def get_zip(
        self, endpoint: str, params: Mapping[str, Any], *, use_cache: bool = True
    ) -> zipfile.ZipFile | None:
        """zip 엔드포인트. 빈 결과(013)는 None."""
        try:
            body = self.get_raw(endpoint, params, use_cache=use_cache)
        except DartError as e:
            if e.code in EMPTY_STATUSES:
                return None
            raise
        return zipfile.ZipFile(io.BytesIO(body))

    def corp_codes(self, *, refresh: bool = False) -> list[CorpCode]:
        """고유번호 전체(상장·비상장). 이 클라이언트 안에서 한 번 받아 들고 있는다."""
        if self._corps is None or refresh:
            self._corps = parse_corp_codes(self.get_raw("corpCode.xml", {}, use_cache=False))
        return self._corps

    def corp_map(self) -> dict[str, str]:
        """`{종목코드: corp_code}` — 상장사만(ET `corp_codes` 반환 모양)."""
        return listed_map(self.corp_codes())

    def corp_code(self, stock_code: str) -> tuple[str, str]:
        """종목코드(6자리) → (corp_code 8자리, 회사명). 없으면 `DartError`."""
        stock_code = str(stock_code).strip().zfill(6)
        for c in self.corp_codes():
            if c.stock_code == stock_code:
                return c.corp_code, c.corp_name
        raise DartError(
            "", f"종목코드 {stock_code} 를 DART 고유번호 목록에서 찾지 못했다", "corpCode.xml"
        )

    def financials(
        self, corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS"
    ) -> list[dict[str, Any]]:
        """단일회사 전체 재무제표. fs_div: CFS(연결) | OFS(별도).

        연결 미작성 회사는 CFS 가 비므로 호출부에서 OFS 로 다시 부른다.
        """
        if reprt_code not in REPRT:
            raise ValueError(f"보고서 코드는 {', '.join(REPRT)} 중 하나: {reprt_code!r}")
        data = self.get_json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )
        return list(data.get("list") or [])

    def disclosures(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str | None = None,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """공시목록 전체 쪽 수집. bgn_de/end_de: 'YYYYMMDD'. 바뀌는 값이라 캐시하지 않는다."""
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params = {
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": str(page),
                "page_count": "100",
            }
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            data = self.get_json("list.json", params, use_cache=False)
            out.extend(data.get("list") or [])
            total_page = str(data.get("total_page") or "1")
            if page >= (int(total_page) if total_page.isdigit() else 1):
                return out
        raise DartError("", f"공시목록이 {max_pages}쪽을 넘었다 — 기간을 나눠 부른다", "list.json")

    def document_texts(self, rcept_no: str) -> list[tuple[str, str]]:
        """공시원문 zip → [(파일명, 본문)]. DART 원문은 EUC-KR 인 경우가 많다."""
        zf = self.get_zip("document.xml", {"rcept_no": rcept_no})
        if zf is None:
            return []
        out: list[tuple[str, str]] = []
        with zf:
            for info in zf.infolist():
                if info.file_size > self._max_bytes:
                    raise DartError("", f"원문 파일이 {self._max_bytes}B 보다 크다", "document.xml")
                raw = zf.read(info)
                for enc in ("utf-8", "euc-kr", "cp949"):
                    try:
                        out.append((info.filename, raw.decode(enc)))
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    out.append((info.filename, raw.decode("utf-8", "replace")))
        return out
