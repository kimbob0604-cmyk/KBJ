"""공개 산출물의 봉투·목록(manifest)·내용 검사 — 공개 사이트로 나가는 JSON 모양은 여기 한 곳.

- 파일 하나 = 봉투 하나(docs/p3_design.md §5.2 와 같은 모양): `source`·`as_of`(시간대 포함
  ISO)·`quality`(ok·stale·estimated·invalid)·`notes`·`generated_at`·`data`. 절대 규칙 1.
- `manifest.json` = `{schema_version, generated_at, files: [{name, source, as_of, quality, sha256}],
  dirs: [{name, count, bytes, digest, source, as_of, quality}]}`. 프런트
  (`web/src/data/static.ts:parseManifest`)·합치기 스크립트(`web/scripts/merge-public-data.mjs`)와
  같은 모양이다 — 목록에 적힌 것만 Pages 로 간다(이름 허용 목록).
- **하위 폴더(P4 — docs/p4_design.md §1.9·§7)**: `fin/<종목코드>.json`(상장사 DART 재무)·
  `reports/<종목코드>.json`(+ `.xlsx` — dart-report 공개 데모)은 파일이 수천 개라 manifest 에
  파일마다 적지 않는다. 대신 **파일 자체가 봉투**(source `DART`·as_of·quality — W 의 `static.ts` 와
  약속)이고, manifest `dirs` 에 폴더마다 개수·합계 크기·요약 해시(`dir_digest` — 정렬한
  `경로<탭>sha256` 줄들의 sha256) 한 항목을 둔다. 종목코드 `^[0-9A-Z]{6}$`, 엑셀은 같은 이름 JSON 의
  `data.xlsx_sha256` 과 맞아야 한다.
- 내용 검사(§7.4): 어느 깊이의 `source` 값에도 로그인 등급 출처 이름(KIS·KRX·ETF_ISSUERS·YAHOO … —
  DATA_TIERS §1)이 나오면 실패. 글자 전체에 로그인 API 흔적(§6.6 번들 검사와 같은 문자열 — 합친
  데이터도 번들 검사를 다시 받는다)이 있어도 실패. 규칙은 JS 쪽과 같게 둔다(`\\b` 는 ASCII 기준).
- **DART 전용 파일**(P4 공시·재무 — `DART_ONLY_NAMES`·하위 폴더 전부): 모든 깊이의 `source` 가
  정확히 `DART` 여야 하고(p4_design §7 '내용 검사'), 관심종목 흔적(이름에 `watch` 가 든 키·로그인
  스키마 이름 `prv_`)이 0 이어야 한다(관심종목은 개인 데이터 — 공개 파일에 표식·목록을 넣지 않는다).
- **P5 공개 파일(docs/p5_design.md §7)**: 하위 폴더 `macro/<이름>.json`(공개 매크로 시리즈·캘린더 —
  MAC)와 평평한 `credit.json`(신용 칩 — MAC). 원천은 공개 출처
  (ECOS·KOSIS·FRED·TREASURY·NYFED·DATAGO …)라 DART 전용이 아니고, 폴더 항목의 `source` 는 폴더
  안 파일 원천들을 쉼표로 이은 것이다. 로그인 출처 이름·관심종목 흔적·`prv_` 글자는 여기서도
  0(하위 폴더 전부와 `P5_FLAT_NAMES`). 크기 상한은 `DIR_CAPS`(폴더 합계)·`FILE_CAPS`(파일
  하나) — 넘으면 실패(자르지 않는다). 숫자는 [제안](설계 §7) — `merge-public-data.mjs` 와 같게 둔다.
- **P6 공개 파일(docs/p6_design.md §7)**: 하위 폴더 `trade/` — 평평한 `trade/<소문자>.json`(속보·
  HS2·국가 총괄·급등·104품목·매핑 종목 랭킹)과 한 단계 더
  `trade/(hs|country|region|stock)/<코드>.json`(장별·국가별·HS6 시군구·종목 겹쳐 보기).
  원천은 관세청(DATAGO)·DART. 이름 규칙은 `SUBDIR_JSON_RE` 하나(`merge-public-data.mjs`·
  `static.ts` 와 같은 정규식 — 시험). 상한은 `DIR_CAPS["trade"]`·`FILE_CAPS`(평평한 파일)·
  `PREFIX_CAPS`(코드 파일 — `file_cap`). 내용 검사(`trade_problems`):
  `origin: local`(VM 관심종목 매핑 — D-P6-14) 0. 옛 `trade/stock/` 시세 칸 규칙은 P9 에서 아래
  `price_problems` 의 한 사례가 됐다.
- **P7 공개 파일(docs/p7_design.md §7)**: 하위 폴더 `diag/` — `diag/<소문자>.json`(summary·history·
  events — 공공지표 진단·매크로 위험 게이지·이벤트, `pub_diag` 만). 상한은 `DIR_CAPS["diag"]`·
  `FILE_CAPS`. 내용 검사(`diag_problems`): 로그인 판정 키(`fear_greed`·`verdict`·`gex*`) 0.
- **원시세 칸(P9 — docs/p9_design.md §3.2 `price_fields`, ADR 0035)**: 공개 JSON 어디에도 키
  `PRICE_KEYS`(close·open·high·low·price·volume·turnover·mktcap·nav·net_value·chg_pct)가 없어야 한다
  (로그인 등급 출처의 가격·거래량·거래대금·시총·NAV·수급 값이 공개로 새는 길을 키 이름으로 막는다).
  예외는 `PRICE_FIELD_ALLOW`(파일·키·값 모양·사유 — 공개 출처 근거가 있는 것만)뿐이고
  `merge-public-data.mjs` 의 같은 표와 한 벌이다(시험).
- 문제 목록에는 값이 아니라 파일 이름·JSON 경로·규칙만 담는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal

from kbj.core.fin_rows import XLSX_MAX_BYTES
from kbj.core.time import KST

__all__ = [
    "DART_ONLY_NAMES",
    "DART_SUBDIRS",
    "DIR_CAPS",
    "FILE_CAPS",
    "LOGIN_SOURCES",
    "MANIFEST_NAME",
    "NAME_RE",
    "P5_FLAT_NAMES",
    "PREFIX_CAPS",
    "PRICE_FIELD_ALLOW",
    "PRICE_KEYS",
    "PUBLIC_FORBIDDEN",
    "QUALITIES",
    "SCHEMA_VERSION",
    "SUBDIRS",
    "SUBDIR_JSON_RE",
    "SUBDIR_XLSX_RE",
    "BlobFile",
    "BuildOut",
    "DirEntry",
    "FileEntry",
    "Manifest",
    "PublicFile",
    "Quality",
    "check_payload",
    "check_tree",
    "dart_only",
    "diag_problems",
    "dir_digest",
    "file_cap",
    "forbidden_hits",
    "iso_kst",
    "listed_paths",
    "login_source_paths",
    "non_dart_source_paths",
    "price_problems",
    "read_manifest",
    "sha256_bytes",
    "sha256_text",
    "trade_problems",
    "watch_trace_paths",
    "worst_quality",
]

SCHEMA_VERSION: Final = 1
MANIFEST_NAME: Final = "manifest.json"
NAME_RE: Final = re.compile(r"[a-z0-9_]+\.json")
# 하위 폴더 — web/src/data/static.ts PUBLIC_SUBDIR·PUBLIC_DOWNLOAD, merge-public-data.mjs 와 같은
# 규칙. P4 fin·reports(종목코드 이름 — DART 전용), P5 macro(소문자 이름 — 공개 출처 여럿),
# P6 trade(평평한 소문자 이름 + (hs|country|region|stock)/<코드> 한 단계 — docs/p6_design.md §7)
# P7 diag(소문자 이름 — 공공지표 진단·게이지·이벤트, docs/p7_design.md §7)
SUBDIRS: Final[tuple[str, ...]] = ("fin", "reports", "macro", "trade", "diag")
DART_SUBDIRS: Final[tuple[str, ...]] = ("fin", "reports")
SUBDIR_JSON_RE: Final = re.compile(
    r"(?:(fin|reports)/[0-9A-Z]{6}|(macro|diag)/[a-z0-9_]+"
    r"|(trade)/(?:(?:hs|country|region|stock)/[0-9A-Z]{2,10}|[a-z0-9_]+))\.json"
)
SUBDIR_XLSX_RE: Final = re.compile(r"reports/[0-9A-Z]{6}\.xlsx")
# P4 공시·재무 목록 파일 — 원천이 DART 하나(§7). merge-public-data.mjs DART_ONLY 와 같은 목록
DART_ONLY_NAMES: Final = frozenset({
    "filings_feed.json",
    "earnings_prelim.json",
    "overhang_recent.json",
    "insider_recent.json",
    "earnings_schedule.json",
    "corp_index.json",
})  # fmt: skip
# P5 평평한 공개 파일 — 관심종목 흔적·로그인 스키마 이름 검사를 받는다(DART 전용은 아니다)
P5_FLAT_NAMES: Final = frozenset({"credit.json", "themes.json"})  # themes.json — 페이지 9(ADR 0039)
_KB: Final = 1024
# P5 크기 상한(바이트, docs/p5_design.md §7 [제안]) — 폴더 합계·파일 하나. merge-public-data.mjs 와
# 같다.
# fin/ 상한은 config/fin.yaml(public_export.fin_max_total_mb)에서 따로 온다.
# P6 trade/ 상한(docs/p6_design.md §7 [제안 — 실측 #57 뒤 확정], config/trade.yaml public_export 와
# 같은 숫자 — 시험이 대조).
# P7 diag/ 상한(docs/p7_design.md §7 [제안] — 250거래일 공개 지표 5개 + 게이지·이벤트, 수십 KB 예상)
DIR_CAPS: Final[dict[str, int]] = {
    "macro": 3 * _KB * _KB, "trade": 20 * _KB * _KB, "diag": 1 * _KB * _KB,
}  # fmt: skip
FILE_CAPS: Final[dict[str, int]] = {
    "credit.json": 100 * _KB,
    "themes.json": 200 * _KB,  # 페이지 9 대테마 분류(ADR 0039 — 수십 개, 약 10 KB)
    "macro/calendar.json": 200 * _KB,
    "trade/flash.json": 200 * _KB,
    "trade/ribbon.json": 4 * _KB,  # 띠 칩(FLASH — credit.json 과 분리, 약 1 KB)
    "trade/hs2.json": 1024 * _KB,
    "trade/countries.json": 1024 * _KB,
    "trade/surge.json": 1024 * _KB,
    "trade/items104.json": 1024 * _KB,
    "trade/monthly.json": 256 * _KB,  # 월별 추이(36달 × 수출·수입 × 류 7 — metrics §29a)
    "trade/stocks.json": 1024 * _KB,
    "diag/summary.json": 100 * _KB,
    "diag/history.json": 512 * _KB,
    "diag/events.json": 100 * _KB,
}
# 코드 이름 파일(하위 폴더 한 단계 더) — 경로 접두 → 파일 하나 상한
PREFIX_CAPS: Final[dict[str, int]] = {
    "trade/hs/": 1024 * _KB,
    "trade/country/": 1024 * _KB,
    "trade/region/": 200 * _KB,
    "trade/stock/": 100 * _KB,
}
# 공개 diag/ 파일에 없어야 하는 로그인 판정 키(국장 공포탐욕·종합 판정·GEX — docs/p7_design.md §7)
_DIAG_LOGIN_KEYS: Final = frozenset({"fear_greed", "verdict"})
# 원시세 칸 — 공개 JSON 어디에도 없어야 하는 키(P9 price_fields — 옛 trade/stock 규칙 D-P6-4
# 를 넓혔다).
# web/scripts/merge-public-data.mjs PRICE_KEYS 와 같다(시험)
PRICE_KEYS: Final = frozenset({
    "close", "open", "high", "low", "price", "volume", "turnover", "mktcap", "nav", "net_value",
    "chg_pct",
})  # fmt: skip
# 예외 — (파일 이름, 키, 값 모양, 사유). 값 모양: "hhmm" = None 또는 'HH:MM' 글자, "number" = None
# 또는 수. 공개 출처 근거가 있는 것만 — 더하려면 ADR 0035 의 절차(출처·사유)를 따른다.
# web/scripts/merge-public-data.mjs PRICE_FIELD_ALLOW 와 같다(시험)
PRICE_FIELD_ALLOW: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("calendar.json", "open", "hhmm", "장 시작 시각(HH:MM) — 가격 아님(KBJ 거래 캘린더 코드 계산)"),
    (
        "calendar.json",
        "close",
        "hhmm",
        "장 마감 시각(HH:MM) — 가격 아님(KBJ 거래 캘린더 코드 계산)",
    ),
    (
        "overhang_recent.json",
        "price",
        "number",
        "DART 주요사항보고서의 전환·행사·발행가액(공개 출처)",
    ),
)
_HHMM: Final = re.compile(r"\d{2}:\d{2}")
Quality = Literal["ok", "stale", "estimated", "invalid"]
QUALITIES: Final[tuple[Quality, ...]] = ("ok", "stale", "estimated", "invalid")
_Q_RANK: Final[dict[str, int]] = {"ok": 0, "stale": 1, "estimated": 2, "invalid": 3}
# web/scripts/merge-public-data.mjs LOGIN_SOURCES 와 같은 이름들. re.ASCII — JS 의 \b 와 같은 경계
# (한글 뒤에 붙은 'KRX가' 도 잡는다). 한글 이름은 이 쪽에서 더 본다.
LOGIN_SOURCES: Final = re.compile(
    r"\b(KIS|KRX|ETF_ISSUERS|YAHOO|FSC_STOCK|FSC_INDEX|FNGUIDE|CONSENSUS|NAVER)\b"
    r"|한국투자|한국거래소|네이버|야후|에프앤가이드",
    re.IGNORECASE | re.ASCII,
)
# web/scripts/check-bundle.mjs PUBLIC_FORBIDDEN 과 같은 문자열 — (needle, 대소문자 무시)
PUBLIC_FORBIDDEN: Final[tuple[tuple[str, bool], ...]] = (
    ("/api", False),
    ("/telegram", False),
    ("auth/login", False),
    ("x-kbj-csrf", True),
    ("KBJ_", False),
    ("kbj_session", True),
)
_DART: Final = "DART"
_WATCH_KEY: Final = re.compile(r"watch", re.IGNORECASE)
_LOGIN_SCHEMA_TEXT: Final = "prv_"
_AWARE_ISO: Final = re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+(Z|[+-]\d{2}:?\d{2})")
_ENVELOPE_KEYS: Final = frozenset({"source", "as_of", "quality", "notes", "generated_at", "data"})


def iso_kst(ts: datetime) -> str:
    """aware 시각 → KST ISO(초 단위, `+09:00`). naive 는 받지 않는다."""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("naive datetime 은 받지 않는다")
    return ts.astimezone(KST).isoformat(timespec="seconds")


def _public_name(name: str) -> bool:
    return name != MANIFEST_NAME and bool(NAME_RE.fullmatch(name) or SUBDIR_JSON_RE.fullmatch(name))


def file_cap(name: str) -> int | None:
    """파일 하나의 크기 상한(바이트) — 이름이 `FILE_CAPS` 에 있거나 `PREFIX_CAPS` 접두면.
    없으면 None."""
    if name in FILE_CAPS:
        return FILE_CAPS[name]
    for prefix, cap in PREFIX_CAPS.items():
        if name.startswith(prefix):
            return cap
    return None


def _key_paths(v: object, pred: Any, path: str = "$") -> list[str]:
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += _key_paths(x, pred, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if pred(str(k), x):  # pyright: ignore[reportUnknownArgumentType]
                out.append(p)
            out += _key_paths(x, pred, p)
    return out


def trade_problems(name: str, raw: object) -> list[str]:
    """P6 `trade/` 파일 내용 검사 — `origin: local` 0(D-P6-14), `trade/stock/` 시세 칸 0(D-P6-4)."""
    if not name.startswith("trade/"):
        return []
    out = [
        f"{name}: 로그인 전용 매핑(origin local — {p})"
        for p in _key_paths(raw, lambda k, x: k == "origin" and x == "local")  # pyright: ignore[reportUnknownLambdaType]
    ]
    return out


# 공개 themes.json 에 없어야 하는 키 — 네이버 유래(원본 테마 목록·편입 종목·사유) 칸(ADR 0039,
# DATA_TIERS §2 9 행: 공개는 대테마 분류만)
_THEME_LOGIN_KEYS: Final = frozenset({
    "naver", "excluded_naver", "members", "member", "reason", "reasons", "code", "codes", "stocks",
})  # fmt: skip


def theme_problems(name: str, raw: object) -> list[str]:
    """페이지 9 `themes.json` 내용 검사 — 네이버 유래 칸(`_THEME_LOGIN_KEYS`) 0."""
    if name != "themes.json":
        return []
    return [
        f"{name}: 로그인 전용 테마 칸({p})"
        for p in _key_paths(raw, lambda k, _x: k in _THEME_LOGIN_KEYS)  # pyright: ignore[reportUnknownLambdaType]
    ]


def _price_shape_ok(kind: str, x: object) -> bool:
    if x is None:
        return True
    if kind == "hhmm":
        return isinstance(x, str) and bool(_HHMM.fullmatch(x))
    return isinstance(x, int | float) and not isinstance(x, bool)


def price_problems(name: str, raw: object) -> list[str]:
    """원시세 칸 검사(P9 price_fields) — `PRICE_KEYS` 키 0, 예외는 `PRICE_FIELD_ALLOW` 의 (파일·키)
    이고 값 모양까지 맞아야 한다(예: 캘린더의 open·close 는 'HH:MM' 글자만)."""
    allow = {(f, k): kind for f, k, kind, _why in PRICE_FIELD_ALLOW}
    out: list[str] = []
    for p in _key_paths(raw, lambda k, _x: k in PRICE_KEYS):  # pyright: ignore[reportUnknownLambdaType]
        key = p.rsplit(".", 1)[1]
        kind = allow.get((name, key))
        if kind is None:
            out.append(f"{name}: 시세 칸({p})")
    for f, k, kind, _why in PRICE_FIELD_ALLOW:
        if f != name:
            continue

        def wrong(kk: str, x: object, k: str = k, kind: str = kind) -> bool:
            return kk == k and not _price_shape_ok(kind, x)

        bad = _key_paths(raw, wrong)
        out += [f"{name}: 시세 칸 예외의 값 모양이 다르다({p} — {kind})" for p in bad]
    return out


def diag_problems(name: str, raw: object) -> list[str]:
    """P7 `diag/` 파일 내용 검사 — 로그인 판정 키(fear_greed·verdict·gex*) 0(p7_design §7)."""
    if not name.startswith("diag/"):
        return []
    return [
        f"{name}: 로그인 판정 키({p})"
        for p in _key_paths(
            raw,
            lambda k, _x: k in _DIAG_LOGIN_KEYS or k.startswith("gex"),  # pyright: ignore[reportUnknownLambdaType]
        )
    ]


def dart_only(name: str) -> bool:
    """원천이 DART 하나여야 하는 파일인가(P4 목록 파일·P4 하위 폴더 fin·reports)."""
    return name in DART_ONLY_NAMES or ("/" in name and name.split("/", 1)[0] in DART_SUBDIRS)


def _trace_checked(name: str) -> bool:
    """관심종목 흔적·로그인 스키마 이름(`prv_`)을 보는 파일인가(DART 전용·하위 폴더·P5)."""
    return dart_only(name) or "/" in name or name in P5_FLAT_NAMES


@dataclass(frozen=True)
class PublicFile:
    """공개 JSON 파일 하나(봉투). `data` 는 JSON 으로 바꿀 수 있는 값만.

    `name` 은 평평한 이름(`calendar.json`) 또는 하위 폴더 경로(`fin/005930.json`). 하위 폴더 파일과
    `compact=True` 는 공백 없이(크기 상한 — §7) 쓴다.
    """

    name: str
    source: str
    as_of: datetime
    quality: Quality
    data: Mapping[str, Any]
    notes: tuple[str, ...] = ()
    compact: bool = False

    def __post_init__(self) -> None:
        if not _public_name(self.name):
            raise ValueError(f"공개 파일 이름 형식: {self.name!r}")
        if not self.source.strip():
            raise ValueError(f"{self.name}: source 가 비었다")
        if self.quality not in QUALITIES:
            raise ValueError(f"{self.name}: 모르는 quality")
        iso_kst(self.as_of)  # naive 거부

    @property
    def subdir(self) -> str | None:
        return self.name.split("/", 1)[0] if "/" in self.name else None

    def envelope(self, generated_at: datetime) -> dict[str, Any]:
        return {
            "source": self.source,
            "as_of": iso_kst(self.as_of),
            "quality": self.quality,
            "notes": list(self.notes),
            "generated_at": iso_kst(generated_at),
            "data": dict(self.data),
        }

    def render(self, generated_at: datetime) -> str:
        """파일 글자(UTF-8, 키 정렬 없음 — 사람이 읽는 순서 그대로, 끝 줄바꿈). NaN·inf 는 오류."""
        env = self.envelope(generated_at)
        if self.compact or self.subdir is not None:
            return (
                json.dumps(env, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
            )
        return json.dumps(env, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


@dataclass(frozen=True)
class BuildOut:
    """공개 파일 묶음 하나(P5 macro·credit)를 만든 결과 — 만든 파일과 만들지 않은 이유(값
    없이). 빈 결과 = 파일 0(빈 파일·가짜 값을 만들지 않는다)."""

    files: tuple[PublicFile, ...] = ()
    skipped: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlobFile:
    """하위 폴더의 바이너리 파일 하나 — 지금은 dart-report 엑셀(`reports/<코드>.xlsx`)뿐."""

    name: str
    body: bytes

    def __post_init__(self) -> None:
        if not SUBDIR_XLSX_RE.fullmatch(self.name):
            raise ValueError(f"공개 바이너리 파일 이름 형식: {self.name!r}")
        if len(self.body) > XLSX_MAX_BYTES:
            raise ValueError(f"{self.name}: {XLSX_MAX_BYTES}B 초과")

    @property
    def subdir(self) -> str:
        return self.name.split("/", 1)[0]


@dataclass(frozen=True)
class FileEntry:
    name: str
    source: str
    as_of: str
    quality: Quality
    sha256: str

    def to_json(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source": self.source,
            "as_of": self.as_of,
            "quality": self.quality,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DirEntry:
    """하위 폴더 한 항목 — 파일마다 적지 않고 개수·합계 크기·요약 해시만(§1.9)."""

    name: str
    count: int
    bytes: int
    digest: str
    source: str
    as_of: str
    quality: Quality

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "count": self.count,
            "bytes": self.bytes,
            "digest": self.digest,
            "source": self.source,
            "as_of": self.as_of,
            "quality": self.quality,
        }


@dataclass(frozen=True)
class Manifest:
    generated_at: datetime
    files: tuple[FileEntry, ...] = ()
    dirs: tuple[DirEntry, ...] = ()
    schema_version: int = SCHEMA_VERSION
    # 만들지 않은 파일과 그 이유(로그·처리기 detail 용 — manifest.json 에는 싣지 않는다)
    skipped: tuple[str, ...] = ()
    names: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "names", tuple(f.name for f in self.files))

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": iso_kst(self.generated_at),
            "files": [f.to_json() for f in self.files],
            "dirs": [d.to_json() for d in self.dirs],
        }

    def render(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False, indent=1) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def dir_digest(items: Iterable[tuple[str, str]]) -> str:
    """하위 폴더 요약 해시 — (경로, 파일 sha256) 들을 경로 순으로 `경로\\t해시\\n` 줄로 이어 sha256.

    merge-public-data.mjs `dirDigest` 와 같은 정의(경로는 `fin/005930.json` 처럼 데이터 루트 기준).
    """
    lines = "".join(f"{p}\t{h}\n" for p, h in sorted(items))
    return sha256_text(lines)


def worst_quality(qs: Iterable[str]) -> Quality:
    """품질 문자열 중 가장 나쁜 것(ok < stale < estimated < invalid). 비면 ok."""
    out: Quality = "ok"
    for q in qs:
        if q in _Q_RANK and _Q_RANK[q] > _Q_RANK[out]:
            out = q  # pyright: ignore[reportAssignmentType]
    return out


def login_source_paths(v: object, path: str = "$") -> list[str]:
    """JSON 안의 모든 `source` 값(깊이 무관)에서 로그인 등급 출처 이름을 찾는다. 경로만 돌려준다."""
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += login_source_paths(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if k == "source" and isinstance(x, str) and LOGIN_SOURCES.search(x):
                out.append(p)
            else:
                out += login_source_paths(x, p)
    return out


def non_dart_source_paths(v: object, path: str = "$") -> list[str]:
    """모든 `source` 값(깊이 무관)이 정확히 `DART` 인지 — 아닌 곳의 경로만(값 없음)."""
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += non_dart_source_paths(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if k == "source":
                if x != _DART:
                    out.append(p)
            else:
                out += non_dart_source_paths(x, p)
    return out


def watch_trace_paths(v: object, path: str = "$") -> list[str]:
    """관심종목 흔적 — 이름에 `watch` 가 든 키의 경로(값 없음)."""
    out: list[str] = []
    if isinstance(v, list):
        for i, x in enumerate(v):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            out += watch_trace_paths(x, f"{path}[{i}]")
    elif isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            p = f"{path}.{k}"
            if _WATCH_KEY.search(str(k)):  # pyright: ignore[reportUnknownArgumentType]
                out.append(p)
            out += watch_trace_paths(x, p)
    return out


def forbidden_hits(text: str) -> list[str]:
    """번들 검사(§6.6)와 같은 금지 문자열 — 찾은 규칙 이름만(값 없음)."""
    low = text.lower()
    return [n for n, ci in PUBLIC_FORBIDDEN if (n.lower() in low if ci else n in text)]


def check_payload(name: str, text: str) -> list[str]:
    """공개 JSON 한 파일의 글자를 검사한다. 문제 목록(빈 목록 = 통과)."""
    problems: list[str] = []
    for hit in forbidden_hits(text):
        problems.append(f"{name}: 로그인 API 흔적 문자열({hit!r} 규칙)")
    try:
        raw: object = json.loads(text)
    except ValueError:
        return [*problems, f"{name}: JSON 이 아님"]
    problems += [f"{name}: source 에 로그인 등급 출처 이름({p})" for p in login_source_paths(raw)]
    if name == MANIFEST_NAME:
        return problems
    if dart_only(name):
        problems += [f"{name}: source 가 DART 가 아님({p})" for p in non_dart_source_paths(raw)]
    if _trace_checked(name):
        problems += [f"{name}: 관심종목 흔적 키({p})" for p in watch_trace_paths(raw)]
        if _LOGIN_SCHEMA_TEXT in text:
            problems.append(f"{name}: 로그인 등급 스키마 이름('{_LOGIN_SCHEMA_TEXT}' 규칙)")
    problems += trade_problems(name, raw)
    problems += theme_problems(name, raw)
    problems += diag_problems(name, raw)
    problems += price_problems(name, raw)
    if not isinstance(raw, dict):
        return [*problems, f"{name}: 봉투가 아님(최상위가 객체가 아님)"]
    env: dict[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    missing = sorted(_ENVELOPE_KEYS - set(env))
    if missing:
        problems.append(f"{name}: 봉투 필드 없음 {missing}")
    if not isinstance(env.get("source"), str) or not env.get("source"):
        problems.append(f"{name}: source 없음")
    for key in ("as_of", "generated_at"):
        val = env.get(key)
        if not isinstance(val, str) or not _AWARE_ISO.fullmatch(val):
            problems.append(f"{name}: {key} 없음·시간대 없음")
    if env.get("quality") not in QUALITIES:
        problems.append(f"{name}: quality 없음·모르는 값")
    return problems


def read_manifest(directory: Path) -> dict[str, Any]:
    path = directory / MANIFEST_NAME
    raw: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("manifest.json 최상위가 객체가 아님")
    return raw  # pyright: ignore[reportUnknownVariableType]


def _objects(manifest: Mapping[str, Any], key: str, *, required: bool) -> list[Mapping[str, Any]]:
    items = manifest.get(key)
    if items is None and not required:
        return []  # P3 manifest(하위 폴더 없음)
    if not isinstance(items, list):
        raise ValueError(f"manifest 에 {key} 목록이 없다")
    out: list[Mapping[str, Any]] = []
    for e in items:  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(e, dict):
            raise ValueError(f"manifest {key} 항목이 객체가 아님")
        out.append(e)  # pyright: ignore[reportUnknownArgumentType]
    return out


def _entries(manifest: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    return _objects(manifest, "files", required=True)


def _dir_files(directory: Path, sub: str) -> tuple[list[str], list[str]]:
    """하위 폴더의 (허용 이름 경로들, 허용하지 않는 이름 경로들) — 데이터 루트 기준 경로, 정렬."""
    ok: list[str] = []
    bad: list[str] = []
    root = directory / sub
    if not root.is_dir():
        return ok, bad
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(directory).as_posix()
        if SUBDIR_JSON_RE.fullmatch(rel) or SUBDIR_XLSX_RE.fullmatch(rel):
            ok.append(rel)
        else:
            bad.append(rel)
    return ok, bad


def _xlsx_claim(text: str) -> str | None:
    """reports JSON 의 `data.xlsx_sha256`(엑셀이 없으면 None)."""
    try:
        env: object = json.loads(text)
    except ValueError:
        return None
    data: object = env.get("data") if isinstance(env, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    claim: object = data.get("xlsx_sha256") if isinstance(data, dict) else None  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return claim if isinstance(claim, str) else None


def _check_dir(directory: Path, entry: Mapping[str, Any]) -> list[str]:
    sub = str(entry.get("name", ""))
    paths, bad = _dir_files(directory, sub)
    problems = [f"{p}: 허용하지 않는 이름(하위 폴더)" for p in bad]
    hashes: list[tuple[str, str]] = []
    total = 0
    sha_of: dict[str, str] = {}
    claims: dict[str, str | None] = {}  # 엑셀 경로 → 짝 JSON 이 적은 sha256
    for rel in paths:
        body = (directory / rel).read_bytes()
        total += len(body)
        sha_of[rel] = sha256_bytes(body)
        hashes.append((rel, sha_of[rel]))
        cap = file_cap(rel)
        if cap is not None and len(body) > cap:
            problems.append(f"{rel}: 크기 상한을 넘는다")
        if rel.endswith(".xlsx"):
            continue
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            problems.append(f"{rel}: UTF-8 이 아님")
            continue
        problems += check_payload(rel, text)
        if rel.startswith("reports/"):
            claims[rel.removesuffix(".json") + ".xlsx"] = _xlsx_claim(text)
    for rel in paths:
        if rel.endswith(".xlsx"):
            claim = claims.get(rel)
            if claim is None:
                problems.append(f"{rel}: 짝 JSON 의 xlsx_sha256 이 없음")
            elif claim != sha_of[rel]:
                problems.append(f"{rel}: 짝 JSON 의 xlsx_sha256 과 다름")
    for xl, claim in claims.items():
        if claim is not None and xl not in sha_of:
            problems.append(f"{xl}: 짝 JSON 은 엑셀이 있다는데 파일이 없음")
    if entry.get("count") != len(paths):
        problems.append(f"{sub}/: manifest 개수와 다름")
    if entry.get("bytes") != total:
        problems.append(f"{sub}/: manifest 합계 크기와 다름")
    if entry.get("digest") != dir_digest(hashes):
        problems.append(f"{sub}/: manifest 요약 해시와 다름")
    if sub in DIR_CAPS and total > DIR_CAPS[sub]:
        problems.append(f"{sub}/: 합계 크기 상한을 넘는다")
    src = entry.get("source")
    if sub in DART_SUBDIRS and src != _DART:
        problems.append(f"{sub}/: source 가 DART 가 아님")
    elif not isinstance(src, str) or not src:
        problems.append(f"{sub}/: source 없음")
    as_of = entry.get("as_of")
    if not isinstance(as_of, str) or not _AWARE_ISO.fullmatch(as_of):
        problems.append(f"{sub}/: as_of 없음·시간대 없음")
    if entry.get("quality") not in QUALITIES:
        problems.append(f"{sub}/: quality 없음·모르는 값")
    return problems


def check_tree(directory: Path) -> list[str]:
    """내보낸 디렉터리 전체 검사 — manifest 와 각 파일(존재·sha256·메타 일치·내용), 하위 폴더
    (개수·크기·요약 해시·각 파일 봉투·DART 전용·엑셀 짝). 문제 목록."""
    try:
        manifest_text = (directory / MANIFEST_NAME).read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{MANIFEST_NAME}: 없음"]
    problems = check_payload(MANIFEST_NAME, manifest_text)
    try:
        manifest = read_manifest(directory)
        entries = _entries(manifest)
        dirs = _objects(manifest, "dirs", required=False)
    except ValueError as e:
        return [*problems, f"{MANIFEST_NAME}: {e}"]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"{MANIFEST_NAME}: schema_version 이 {SCHEMA_VERSION} 이 아님")
    gen = manifest.get("generated_at")
    if not isinstance(gen, str) or not _AWARE_ISO.fullmatch(gen):
        problems.append(f"{MANIFEST_NAME}: generated_at 없음·시간대 없음")
    listed: set[str] = set()
    for e in entries:
        name = str(e.get("name", ""))
        if not NAME_RE.fullmatch(name) or name == MANIFEST_NAME:
            problems.append(f"{name or '(이름 없음)'}: 허용하지 않는 이름")
            continue
        listed.add(name)
        try:
            text = (directory / name).read_text(encoding="utf-8")
        except FileNotFoundError:
            problems.append(f"{name}: manifest 에 있으나 파일이 없음")
            continue
        if e.get("sha256") != sha256_text(text):
            problems.append(f"{name}: sha256 불일치")
        if name in FILE_CAPS and len(text.encode("utf-8")) > FILE_CAPS[name]:
            problems.append(f"{name}: 크기 상한을 넘는다")
        problems += check_payload(name, text)
        try:
            env: object = json.loads(text)
        except ValueError:
            continue
        if isinstance(env, dict):
            for key in ("source", "as_of", "quality"):
                if env.get(key) != e.get(key):  # pyright: ignore[reportUnknownMemberType]
                    problems.append(f"{name}: manifest 의 {key} 와 파일 봉투가 다름")
    extra = sorted(
        p.name for p in directory.glob("*.json") if p.name not in listed | {MANIFEST_NAME}
    )
    problems += [f"{n}: manifest 에 없는 파일(공개로 나가지 않는다 — 지울 것)" for n in extra]
    seen: set[str] = set()
    for d in dirs:
        sub = str(d.get("name", ""))
        if sub not in SUBDIRS or sub in seen:
            problems.append(f"{sub or '(이름 없음)'}/: 허용하지 않는(또는 겹친) 하위 폴더")
            continue
        seen.add(sub)
        problems += _check_dir(directory, d)
    for sub in SUBDIRS:
        if sub not in seen and any(_dir_files(directory, sub)):
            problems.append(f"{sub}/: manifest 에 없는 하위 폴더(공개로 나가지 않는다 — 지울 것)")
    return problems


def listed_paths(directory: Path) -> list[str]:
    """manifest 가 덮는 파일 경로 전부(평평한 파일 + 적힌 하위 폴더의 허용 이름 파일). manifest
    제외."""
    manifest = read_manifest(directory)
    out = [str(e["name"]) for e in _entries(manifest)]
    for d in _objects(manifest, "dirs", required=False):
        sub = str(d.get("name", ""))
        if sub in SUBDIRS:
            out += _dir_files(directory, sub)[0]
    return out
