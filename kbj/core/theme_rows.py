"""테마 마스터(페이지 9 — 테마·밸류체인)의 행 자료형 — 0023 `pub_themes.*`·`prv_themes.*`.

근거: ADR 0039(테마 마스터 받아 쓰기, 사용자 결정 2026-10-15), docs/DATA_TIERS.md §1·§2 9 행,
docs/metrics.md §31. 저장소(`kbj.store.repos.theme`)·엔진(`kbj.engines.theme`)·수집 처리기
(`kbj.services.collectors.theme_master`)·API 가 같이 쓴다.

- 원천은 stock-dashboard 레포가 매주 만드는 '테마 마스터'(`data/master/`) 하나다. KBJ 는 네이버에
  직접 접속하지 않는다(CLAUDE.md §2). 대테마 분류(사용자가 만든 id·이름·섹션·밸류체인)만 공개 등급
  (`pub_themes.theme`)이고, 네이버에서 온 것(테마 이름·설명·편입 종목·편입 사유·종목 마스터의
  업종)은 로그인 등급(`prv_themes.*`)이다 — 등급은 표(스키마)로 나눈다(ADR 0002).
- 모든 행은 `source`·`as_of`·`quality`(절대 규칙 1). `as_of` = 원천 `meta.json built_at`(그 판을
  만든 시각, tz 필수). 공개 행의 source 는 `SOURCE_MASTER`, 로그인 행은 원천의 원천까지 적은
  `SOURCE_LOGIN`.
- 이 모듈은 `kbj.core` 밖을 모른다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final

from kbj.core.quality import Quality

__all__ = [
    "SOURCE_LOGIN",
    "SOURCE_MASTER",
    "SOURCE_UPSTREAM",
    "IndustryGroupRow",
    "MasterLoad",
    "MemberRow",
    "NaverExcludedRow",
    "NaverThemeRow",
    "StockRow",
    "ThemeLinkRow",
    "ThemeMaster",
    "ThemeRow",
    "TypeOverrideRow",
    "code_ok",
    "theme_id_ok",
]

# 원천 표기(ADR 0039 §2) — 공개 행·공개 파일은 SOURCE_MASTER 만(네이버 이름을 공개 source 칸에 두지
# 않는다 — 공개 내보내기 LOGIN_SOURCES 검사). 로그인 행은 원천의 원천을 함께 적는다.
SOURCE_MASTER: Final = "stock-dashboard:data/master"
SOURCE_UPSTREAM: Final = "naver(stock-dashboard 수집)"
SOURCE_LOGIN: Final = f"{SOURCE_MASTER}+{SOURCE_UPSTREAM}"

_THEME_ID: Final = re.compile(r"[a-z0-9][a-z0-9_]{0,63}")
_CODE: Final = re.compile(r"[0-9A-Z]{6}")
_CHAIN: Final = re.compile(r"[A-Za-z0-9_]{1,64}")


def theme_id_ok(v: object) -> bool:
    return isinstance(v, str) and _THEME_ID.fullmatch(v) is not None


def code_ok(v: object) -> bool:
    return isinstance(v, str) and _CODE.fullmatch(v) is not None


def _text(name: str, v: object, *, optional: bool = False) -> None:
    if v is None and optional:
        return
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{name}: 빈 글자")


def _aware(name: str, v: object) -> None:
    if not isinstance(v, datetime) or v.tzinfo is None or v.utcoffset() is None:
        raise ValueError(f"{name}: 시간대가 있는 시각이어야 한다")


def _sq(row: Any) -> None:
    """source·as_of·quality(저장소에서 읽은 글자 'ok' 는 Quality 로 바꾼다)."""
    _text("source", row.source)
    _aware("as_of", row.as_of)
    try:
        object.__setattr__(row, "quality", Quality(row.quality))
    except ValueError:
        raise ValueError("quality: ok·stale·estimated·invalid 가 아니다") from None


def _nonneg(name: str, v: object, *, optional: bool = False) -> None:
    if v is None and optional:
        return
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError(f"{name}: 0 이상 정수가 아니다")


def _pos(name: str, v: object) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
        raise ValueError(f"{name}: 양의 정수가 아니다")


@dataclass(frozen=True)
class ThemeRow:
    """대테마 하나(`pub_themes.theme` — 공개). 사용자가 만든 분류: id·이름·섹션·밸류체인(없으면
    None)과 원천 순서(`ord`)."""

    id: str
    name: str
    section: str
    valuechain: str | None
    ord: int
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        if not theme_id_ok(self.id):
            raise ValueError(f"대테마 id 형식: {self.id[:40]!r}")
        _text("name", self.name)
        _text("section", self.section)
        if self.valuechain is not None and _CHAIN.fullmatch(self.valuechain) is None:
            raise ValueError(f"valuechain 형식: {self.valuechain[:40]!r}")
        _nonneg("ord", self.ord)
        _sq(self)


@dataclass(frozen=True)
class NaverThemeRow:
    """네이버 테마 하나(`prv_themes.naver_theme` — 로그인). count = 네이버 테마 전체 종목 수(ETF
    포함), in_master = 종목 마스터 안 종목 수(원천 그대로 — 모르면 None)."""

    no: int
    name: str
    description: str | None
    count: int | None
    in_master: int | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _pos("no", self.no)
        _text("name", self.name)
        _nonneg("count", self.count, optional=True)
        _nonneg("in_master", self.in_master, optional=True)
        _sq(self)


@dataclass(frozen=True)
class ThemeLinkRow:
    """대테마 ↔ 네이버 테마(`prv_themes.theme_link` — 로그인: 네이버 테마 이름이 든다). 한 네이버
    테마가 대테마 둘에 걸릴 수 있다. `naver_name` 은 themes.json 에 적힌 이름(사람이 읽는 용)."""

    theme_id: str
    naver_no: int
    naver_name: str
    ord: int
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        if not theme_id_ok(self.theme_id):
            raise ValueError(f"대테마 id 형식: {self.theme_id[:40]!r}")
        _pos("naver_no", self.naver_no)
        _text("naver_name", self.naver_name)
        _nonneg("ord", self.ord)
        _sq(self)


@dataclass(frozen=True)
class NaverExcludedRow:
    """대테마로 옮기지 않은 네이버 테마(이벤트·계절·일회성 — `prv_themes.naver_excluded`)."""

    naver_no: int
    naver_name: str
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _pos("naver_no", self.naver_no)
        _text("naver_name", self.naver_name)
        _sq(self)


@dataclass(frozen=True)
class MemberRow:
    """네이버 테마 × 종목 편입 한 줄(`prv_themes.member` — 로그인). reason = 편입 사유(원천 그대로,
    없으면 None)."""

    naver_no: int
    code: str
    reason: str | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _pos("naver_no", self.naver_no)
        if not code_ok(self.code):
            raise ValueError(f"종목코드 형식: {self.code[:10]!r}")
        _text("reason", self.reason, optional=True)
        _sq(self)


@dataclass(frozen=True)
class StockRow:
    """종목 마스터 한 줄(`prv_themes.stock` — 로그인: 업종이 네이버 표기). type = common·preferred·
    reit·infra …(원천 그대로), tier = 검수 우선순위 A·B·C(없으면 None), parent_code = 우선주의
    보통주 코드."""

    code: str
    name: str
    market: str
    type: str
    parent_code: str | None
    industry: str | None
    industry_group: str | None
    tier: str | None
    former_names: str | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        if not code_ok(self.code):
            raise ValueError(f"종목코드 형식: {self.code[:10]!r}")
        _text("name", self.name)
        _text("market", self.market)
        _text("type", self.type)
        if self.parent_code is not None and not code_ok(self.parent_code):
            raise ValueError(f"parent_code 형식: {self.parent_code[:10]!r}")
        for n in ("industry", "industry_group", "tier", "former_names"):
            _text(n, getattr(self, n), optional=True)
        _sq(self)


@dataclass(frozen=True)
class IndustryGroupRow:
    """업종 대분류(`prv_themes.industry_group` — 로그인: 업종 이름이 네이버 표기)."""

    id: str
    name: str
    industries: tuple[str, ...]
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _text("id", self.id)
        _text("name", self.name)
        object.__setattr__(self, "industries", tuple(self.industries))
        for x in self.industries:
            _text("industry", x)
        _sq(self)


@dataclass(frozen=True)
class TypeOverrideRow:
    """종목 종류 손 지정(`prv_themes.type_override` — 원천 type_overrides.csv)."""

    code: str
    type: str
    reason: str | None
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        if not code_ok(self.code):
            raise ValueError(f"종목코드 형식: {self.code[:10]!r}")
        _text("type", self.type)
        _text("reason", self.reason, optional=True)
        _sq(self)


@dataclass(frozen=True)
class MasterLoad:
    """적재한 판 하나(`prv_themes.master_load`). built_at = 원천 meta.json built_at(바뀜 감지 키),
    digest = 받은 파일들의 sha256(이름순), counts = 표마다 행 수, warnings = 적재는 했지만 알릴 것
    (대테마가 가리키는 없는 네이버 테마 번호, 대테마·제외 어디에도 없는 새 네이버 테마 …)."""

    built_at: datetime
    digest: str
    counts: Mapping[str, int]
    warnings: tuple[str, ...]
    source: str
    as_of: datetime
    quality: Quality

    def __post_init__(self) -> None:
        _aware("built_at", self.built_at)
        object.__setattr__(self, "counts", dict(self.counts))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if not re.fullmatch(r"[0-9a-f]{64}", self.digest):
            raise ValueError("digest: sha256 16진 64자가 아니다")
        for k, v in self.counts.items():
            _text("counts 키", k)
            _nonneg(f"counts[{k}]", v)
        _sq(self)


@dataclass(frozen=True)
class ThemeMaster:
    """검증을 통과한 판 하나 — 저장소가 한 트랜잭션으로 통째로 바꾼다(`replace_master`)."""

    load: MasterLoad
    themes: tuple[ThemeRow, ...]
    links: tuple[ThemeLinkRow, ...]
    excluded: tuple[NaverExcludedRow, ...]
    naver_themes: tuple[NaverThemeRow, ...]
    members: tuple[MemberRow, ...]
    stocks: tuple[StockRow, ...]
    groups: tuple[IndustryGroupRow, ...]
    overrides: tuple[TypeOverrideRow, ...] = field(default=())

    @property
    def built_at(self) -> datetime:
        return self.load.built_at
