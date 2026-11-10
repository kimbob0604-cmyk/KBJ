"""공개 사이트 내보내기 — Beom's Dashboard 방식(사용자 결정 2026-11-09,
docs/adr/0042-site-snapshot.md).

로그인 화면의 페이지 1~10 이 부르는 GET API 응답을 앱 안에서 한 번씩 불러(audit 와 같은 세션
우회 — 서버·쿠키를 건드리지 않는다) 정적 JSON 파일로 쓴다. 맥이 만들고 GitHub Pages(kbj-site
저장소)가 내보낸다(`scripts/local/kbj.sh site`). 맥이 꺼져 있어도 마지막으로 올린 화면은 열린다.

- 개인 기능(11 규칙·12 백테스트·저널·포트폴리오·13 운영)·관심종목·인증은 내보내지 않는다
  (`SKIP`). 응답 안의 관심종목 표시(`is_watch`)는 false 로 지운다 — 무엇을 보는지가 드러나지 않게.
  (관심종목은 방문자 브라우저에만 둔다 — web/src/data/snapshot.ts.)
- 파일 이름(`site_file`)은 web/src/data/snapshot.ts 의 siteFile 과 같은 규칙이다: `<경로>.json`,
  쿼리가 있으면 `<경로>@<키>-<값>.<키>-<값>.json`(키 정렬·빈 값 생략·[A-Za-z0-9_-] 밖 문자는 '_').
- 404 `no_data` 본문도 파일로 쓴다 — 화면이 서버 안내 문구('아직 없음 — 작업 …')를 그대로
  보인다. 그 밖의 4xx·5xx 는 쓰지 않고 결과에 센다(에러를 삼키지 않는다 — 결과 줄에 남긴다).
- 묶음: `live`(장중 10분 — config/site.yaml 의 live 경로 + 그 경로의 grid), `full`(인자 없는 경로
  전부 + grid + 화면에 나온 종목의 상세(+ stock_grid) + 테마 상세 + 전 종목 목록).
- 펼침(`SiteGrid` — 화면 버튼 조합): 인자마다 값 목록, 모든 조합을 부른다. null 은 인자를
  빼고, API 기본값과 같은 값도 뺀다(화면도 빼고 찾는다 — 같은 파일을 두 번 만들지 않는다).
  값 '@today'·'@today-N' 은 내보내는 시각의 KST 날짜. 값 자리의 {source, list, field, limit}
  은 다른 응답 목록에서 모은 값이고, 키의 `{이름}` 자리는 경로 인자다. 조합 수 상한
  `GRID_MAX`(설정을 읽을 때 검사).
- 종목: 내보낸 응답에 나온 종목 코드(`code` 칸)를 많이 나온 순으로 `max_stocks` 개 + `always`
  (펼침 응답은 펼침 하나에 한 번만 센다 — 조합이 많은 펼침이 순위를 끌고 가지 않게). 종목 상세가
  404 인 코드는 나머지 상세를 부르지 않는다.
- 전 종목 목록(`stock/search-index.json`): readers.stock.stock_index(검색과 같은 규칙) + 줄마다 상세
  파일이 있는가(`detail`). 화면 검색·상세 없음 안내가 이것을 쓴다.
"""

from __future__ import annotations

import io
import itertools
import json
import re
import tarfile
import time
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlencode

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbj.core.time import kst_date
from kbj.services.api.audit import audit_client
from kbj.services.api.deps import ApiState
from kbj.services.api.models.common import NoDataBody
from kbj.services.api.readers._common import NoData
from kbj.services.api.readers.stock import stock_index

__all__ = [
    "GRID_MAX",
    "SKIP",
    "Harvest",
    "SiteConfig",
    "SiteFile",
    "SiteGrid",
    "SiteReport",
    "SiteRequest",
    "base_requests",
    "build_site",
    "check_public",
    "codes_in",
    "grid_plan",
    "grid_requests",
    "harvest",
    "load_site_config",
    "query_defaults",
    "scrub",
    "site_file",
    "write_dir",
    "write_tar",
]

# 내보내지 않는 경로 — 인증·웹훅·개인 기능(규칙·백테스트·저널·포트폴리오·관심종목·운영)·
# 상태 확인·검색(q 필수 — 검색은 `stock/search-index.json` 으로 화면이 거른다)
SKIP: Final = (
    "/api/auth",
    "/api/webhook",
    "/api/rules",
    "/api/backtest",
    "/api/journal",
    "/api/portfolio",
    "/api/watchlist",
    "/api/ops",
    "/api/health",
    "/api/stock/search",
)
CODE_RE: Final = re.compile(r"^[0-9A-Z]{6}$")
THEME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_UNSAFE: Final = re.compile(r"[^A-Za-z0-9_-]")
SEARCH_INDEX: Final = "stock/search-index"
WATCH_KEYS: Final = frozenset({"is_watch"})
# 펼침 하나의 조합 수 상한(다른 응답에서 모으는 값은 그 limit 로 센다) — 설정을 읽을 때 넘으면 오류
GRID_MAX: Final = 200
HARVEST_MAX: Final = 100  # 다른 응답에서 모으는 값 수의 기본 상한(목록 앞에서부터 — 화면 표 순서)
TODAY_RE: Final = re.compile(r"^@today(?:-(\d{1,3}))?$")  # 내보내는 시각의 KST 날짜(−N 달력일)
PATH_VALUE_RE: Final = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # 경로 인자 값(한 마디 — '/'·'.' 없음)
PARAM_RE: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")
_SLOT_RE: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_GRID_KEY_RE: Final = re.compile(
    r"^[a-z0-9_-]+(?:/(?:[A-Za-z0-9_-]+|\{[A-Za-z_][A-Za-z0-9_]*\}))*$"
)

Param = tuple[str, str]
SetName = Literal["live", "full"]


def _text(v: object) -> str:
    """쿼리 값 문자열 — 화면(String(v))과 같게: bool 은 소문자."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def site_file(key: str, params: Mapping[str, object] | None = None) -> str:
    """API 경로 + 쿼리 → 사이트 파일 이름(web/src/data/snapshot.ts siteFile 과 같은 규칙)."""
    parts: list[str] = []
    for k in sorted(params or {}):
        v = (params or {})[k]
        if v is None or v == "":
            continue
        parts.append(f"{_UNSAFE.sub('_', k)}-{_UNSAFE.sub('_', _text(v))}")
    return f"{key}@{'.'.join(parts)}.json" if parts else f"{key}.json"


@dataclass(frozen=True, slots=True)
class SiteRequest:
    key: str  # API 경로('/api/' 뒤) — 'market/summary'
    params: tuple[Param, ...] = ()

    @property
    def url(self) -> str:
        q = urlencode(sorted(self.params))
        return f"/api/{self.key}" + (f"?{q}" if q else "")

    @property
    def file(self) -> str:
        return site_file(self.key, dict(self.params))


@dataclass(frozen=True, slots=True)
class SiteFile:
    name: str
    body: bytes


@dataclass(slots=True)
class SiteReport:
    set: SetName
    files: int = 0
    bytes: int = 0
    no_data: int = 0
    stocks: int = 0
    failed: dict[str, int] = field(default_factory=dict[str, int])  # 'key@…' → 상태 코드
    # 경로 모양('stock/{code}/chart') → 초
    seconds: dict[str, float] = field(default_factory=dict[str, float])
    # 펼침 키 → 형식이 맞지 않아 부르지 않은 경로 값 수(다른 응답에서 모은 값 — 에러를
    # 삼키지 않게 센다)
    skipped: dict[str, int] = field(default_factory=dict[str, int])

    def line(self) -> str:
        bad = f" · 실패 {len(self.failed)}" if self.failed else ""
        odd = f" · 형식 밖 값 {sum(self.skipped.values())}" if self.skipped else ""
        return (
            f"site {self.set}: 파일 {self.files}개 · {self.bytes / 1e6:.1f} MB · 아직 없음 "
            f"{self.no_data} · 종목 {self.stocks} · {sum(self.seconds.values()):.0f}초{bad}{odd}"
        )

    def slowest(self, n: int = 8) -> list[str]:
        top = sorted(self.seconds.items(), key=lambda kv: -kv[1])[:n]
        return [f"{k} {v:.0f}초" for k, v in top]


@dataclass(frozen=True, slots=True)
class Harvest:
    """값을 다른 응답에서 모은다 — `source` 경로(인자 없는 파일)의 data → `list`(점으로 잇는
    칸 이름) 목록 → 항목의 `field` 문자열 칸. 목록 앞에서부터 `limit` 개(화면 표 순서 그대로)."""

    source: str
    list: str
    field: str
    limit: int = HARVEST_MAX


GridValue = str | Harvest | None  # None = 인자를 뺀다, '@today-N' = 날짜 토큰


@dataclass(frozen=True, slots=True)
class SiteGrid:
    """화면 버튼 조합 — 인자마다 값 목록, 모든 조합을 부른다. 키의 `{이름}` 자리는 경로 인자
    ('trade/items/{hs}/series'). 종목마다 펼침(stock_grid)은 `{code}` 를 상세 종목 코드로 채운다."""

    key: str
    params: tuple[tuple[str, tuple[GridValue, ...]], ...] = ()

    @property
    def slots(self) -> tuple[str, ...]:
        return tuple(_SLOT_RE.findall(self.key))

    def bound(self) -> int:
        """조합 수 상한(다른 응답에서 모으는 값은 limit 개로)."""
        n = 1
        for _name, values in self.params:
            n *= sum(v.limit if isinstance(v, Harvest) else 1 for v in values)
        return n


@dataclass(frozen=True, slots=True)
class SiteConfig:
    max_stocks: int = 400
    always: tuple[str, ...] = ()
    live: tuple[str, ...] = ()
    grid: tuple[SiteGrid, ...] = ()
    stock_grid: tuple[SiteGrid, ...] = ()  # 상세를 만드는 종목마다(`{code}`)


def _grid_value(v: object, where: str) -> GridValue:
    if v is None:
        return None
    if isinstance(v, Mapping):
        m: Mapping[str, Any] = v  # pyright: ignore[reportUnknownVariableType]
        source, lst, fld = m.get("source"), m.get("list", ""), m.get("field")
        limit = m.get("limit", HARVEST_MAX)
        if not isinstance(source, str) or not _GRID_KEY_RE.match(source) or "{" in source:
            raise ValueError(f"{where}: 모으는 값의 source 는 인자 없는 경로")
        if not isinstance(lst, str) or not isinstance(fld, str) or not fld:
            raise ValueError(f"{where}: 모으는 값은 {{source, list, field, limit}}")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= HARVEST_MAX:
            raise ValueError(f"{where}: limit 은 1~{HARVEST_MAX}")
        return Harvest(source, lst, fld, limit)
    if isinstance(v, (list, tuple, set)):
        raise ValueError(f"{where}: 값 목록 안에 목록")
    text = _text(v)
    if text.startswith("@") and not TODAY_RE.match(text):
        raise ValueError(f"{where}: 모르는 값 토큰 {text!r}('@today'·'@today-N' 만)")
    return text


def _grid(raw: object, where: str, *, stock: bool) -> SiteGrid:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("key"), str):  # pyright: ignore[reportUnknownMemberType]
        raise ValueError(f"{where} 항목은 {{key, params}}")
    entry: Mapping[str, Any] = raw  # pyright: ignore[reportUnknownVariableType]
    key = str(entry["key"])
    if not _GRID_KEY_RE.match(key):
        raise ValueError(f"{where} {key}: 경로 형식")
    raw_params: object = entry.get("params") or {}
    if not isinstance(raw_params, Mapping):
        raise ValueError(f"{where} {key}: params 는 {{인자: [값…]}}")
    params: list[tuple[str, tuple[GridValue, ...]]] = []
    for name, vals in raw_params.items():  # pyright: ignore[reportUnknownVariableType]
        pname = str(name)  # pyright: ignore[reportUnknownArgumentType]
        if not PARAM_RE.match(pname):
            raise ValueError(f"{where} {key}: 인자 이름 {pname!r}")
        seq: list[object] = list(vals) if isinstance(vals, list) else [vals]  # pyright: ignore[reportUnknownArgumentType]
        values = tuple(dict.fromkeys(_grid_value(v, f"{where} {key}.{pname}") for v in seq))
        if not values:
            raise ValueError(f"{where} {key}.{pname}: 값이 없다")
        params.append((pname, values))
    g = SiteGrid(key, tuple(params))
    slots = set(g.slots)
    names = {n for n, _ in params}
    need = slots - {"code"} if stock else slots
    if stock and "code" not in slots:
        raise ValueError(f"{where} {key}: 종목마다 펼침은 키에 {{code}} 가 있어야 한다")
    if stock and "code" in names:
        raise ValueError(
            f"{where} {key}: {{code}} 는 상세 종목 코드로 채운다(params 에 두지 않는다)"
        )
    if need - names:
        raise ValueError(f"{where} {key}: 경로 자리 {sorted(need - names)} 의 값이 없다")
    for pname, values in params:
        if pname in slots:
            bad = [
                v
                for v in values
                if v is None or (isinstance(v, str) and not PATH_VALUE_RE.match(v))
            ]
            if bad:
                raise ValueError(f"{where} {key}.{pname}: 경로 인자 값 형식 {bad}")
    if g.bound() > GRID_MAX:
        raise ValueError(f"{where} {key}: 조합 {g.bound()}개 — 상한 {GRID_MAX}")
    return g


def load_site_config(path: Path) -> SiteConfig:
    """config/site.yaml — 없으면 기본값(묶음 live 는 비어 있다)."""
    if not path.exists():
        return SiteConfig()
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: 최상위가 표가 아니다")
    old = [k for k in ("variants", "expand") if k in raw]
    if old:
        raise ValueError(f"{path.name}: {old} 는 grid 로 바뀌었다(config/site.yaml 머리 주석)")
    always = tuple(str(c) for c in raw.get("always") or ())
    bad = [c for c in always if not CODE_RE.match(c)]
    if bad:
        raise ValueError(f"{path.name}: always 에 종목 코드가 아닌 값 {bad}")
    grid = tuple(_grid(g, f"{path.name}: grid", stock=False) for g in raw.get("grid") or ())
    stock_grid = tuple(
        _grid(g, f"{path.name}: stock_grid", stock=True) for g in raw.get("stock_grid") or ()
    )
    return SiteConfig(
        max_stocks=int(raw.get("max_stocks", 400)),
        always=always,
        live=tuple(str(k) for k in raw.get("live") or ()),
        grid=grid,
        stock_grid=stock_grid,
    )


def _get_paths(app: FastAPI) -> list[str]:
    out: list[str] = []
    for p, ops in app.openapi().get("paths", {}).items():
        if "get" not in ops or not p.startswith("/api/") or p.startswith(SKIP):
            continue
        if p.endswith(".xlsx"):
            continue
        out.append(p)
    return sorted(out)


def _required_query(app: FastAPI, path: str) -> bool:
    get = app.openapi()["paths"][path]["get"]
    return any(p.get("in") == "query" and p.get("required") for p in get.get("parameters", []))


def base_requests(app: FastAPI) -> list[SiteRequest]:
    """인자 없는 GET 경로 전부(기본 쿼리) — 화면이 처음 열 때 부르는 모양. 필수 쿼리가 있는 경로
    (diag/history?key= 등)는 빼고 설정의 `grid` 로만 부른다."""
    return [
        SiteRequest(p.removeprefix("/api/"))
        for p in _get_paths(app)
        if "{" not in p and not _required_query(app, p)
    ]


def query_names(app: FastAPI) -> dict[str, frozenset[str]]:
    """경로(틀 그대로)마다 쿼리 인자 이름 — 펼침 설정의 인자 이름 오타를 잡는다."""
    out: dict[str, frozenset[str]] = {}
    for p in _get_paths(app):
        get = app.openapi()["paths"][p]["get"]
        names = [prm["name"] for prm in get.get("parameters", []) if prm.get("in") == "query"]
        out[p.removeprefix("/api/")] = frozenset(names)
    return out


def code_templates(app: FastAPI) -> list[str]:
    """`{code}` 하나만 든 경로 — 'stock/{code}/header' 꼴(ETF 펀드 구성은 빼고)."""
    out: list[str] = []
    for p in _get_paths(app):
        key = p.removeprefix("/api/")
        if key.count("{") == 1 and "{code}" in key and not key.startswith("etf/fund/"):
            out.append(key)
    # 머리를 먼저 — 404(종목 아님)면 나머지를 부르지 않는다
    return sorted(out, key=lambda k: (k != "stock/{code}/header", k))


def query_defaults(app: FastAPI) -> dict[str, dict[str, Any]]:
    """경로(틀 그대로 — 'stock/{code}/valuation')마다 기본값이 있는 쿼리 인자 — `_defaults.json`.

    화면은 기본값과 같은 인자를 빼고 파일을 찾는다(snapshot.ts) — 내보내기도 빼고 부른다.
    """
    out: dict[str, dict[str, Any]] = {}
    for p, ops in app.openapi().get("paths", {}).items():
        if not p.startswith("/api/") or p.startswith(SKIP) or "get" not in ops:
            continue
        d: dict[str, Any] = {}
        for prm in ops["get"].get("parameters", []):
            schema = prm.get("schema", {})
            if prm.get("in") == "query" and schema.get("default") is not None:
                d[prm["name"]] = schema["default"]
        if d:
            out[p.removeprefix("/api/")] = d
    return out


def scrub(v: Any) -> Any:
    """관심종목 표시를 지운다(`is_watch` → false) — 개인 데이터가 공개 파일에 실리지 않게."""
    if isinstance(v, dict):
        return {k: (False if k in WATCH_KEYS else scrub(x)) for k, x in v.items()}  # pyright: ignore[reportUnknownVariableType]
    if isinstance(v, list):
        return [scrub(x) for x in v]  # pyright: ignore[reportUnknownVariableType]
    return v


def codes_in(v: Any, out: Counter[str]) -> None:
    """응답 안의 종목 코드(`code` 칸) — 화면에 나온 종목."""
    if isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            if k == "code" and isinstance(x, str) and CODE_RE.match(x):
                out[x] += 1
            else:
                codes_in(x, out)
    elif isinstance(v, list):
        for x in v:  # pyright: ignore[reportUnknownVariableType]
            codes_in(x, out)


def _ids_in(v: Any, out: list[str]) -> None:
    if isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            if k == "id" and isinstance(x, str) and THEME_RE.match(x) and x not in out:
                out.append(x)
            else:
                _ids_in(x, out)
    elif isinstance(v, list):
        for x in v:  # pyright: ignore[reportUnknownVariableType]
            _ids_in(x, out)


def harvest(body: Any, list_path: str, field_name: str) -> list[str]:
    """응답 data 의 `list_path`(점으로 잇는 칸 이름) 목록에서 항목의 `field_name` 문자열 값."""
    node: Any = body.get("data") if isinstance(body, dict) else None
    for part in [x for x in list_path.split(".") if x]:
        node = node.get(part) if isinstance(node, dict) else None  # pyright: ignore[reportUnknownMemberType]
    out: list[str] = []
    for item in node if isinstance(node, list) else []:  # pyright: ignore[reportUnknownVariableType]
        v = item.get(field_name) if isinstance(item, dict) else None  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        if isinstance(v, str) and v and v not in out:
            out.append(v)
    return out


def resolve_token(v: str, today: date) -> str:
    """'@today'·'@today-N' → KST 날짜 'YYYY-MM-DD'(달력일 — 화면의 '어제'도 달력일 −1).
    그 밖은 그대로."""
    m = TODAY_RE.match(v)
    if m is None:
        return v
    return (today - timedelta(days=int(m.group(1) or 0))).isoformat()


def grid_requests(
    g: SiteGrid,
    files: Mapping[str, SiteFile],
    *,
    today: date,
    defaults: Mapping[str, Mapping[str, Any]],
    code: str | None = None,
) -> tuple[list[SiteRequest], int]:
    """펼침 하나의 요청(조합 순서 그대로, 같은 파일은 한 번) — (요청, 형식이 맞지 않아 뺀 경로
    값 수).

    `defaults`(query_defaults — 키 틀마다)와 같은 값·null 은 인자를 뺀다. 모으는 값은
    `files`(이번 묶음에서 이미 받은 응답)에서 — 원천 파일이 없으면 그 값은 없다. `code` 는
    종목마다 펼침의 `{code}`."""
    key = g.key if code is None else g.key.replace("{code}", code)
    slots = set(_SLOT_RE.findall(key))
    d = defaults.get(g.key, {})
    skipped = 0
    axes: list[list[tuple[str, str | None]]] = []
    for name, values in g.params:
        vals: list[str | None] = []
        for v in values:
            if isinstance(v, Harvest):
                src = files.get(site_file(v.source))
                got = harvest(json.loads(src.body), v.list, v.field) if src is not None else []
                vals += got[: v.limit]
            else:
                vals.append(None if v is None else resolve_token(v, today))
        vals = list(dict.fromkeys(vals))
        if name in slots:
            good = [x for x in vals if x is not None and PATH_VALUE_RE.match(x)]
            skipped += len(vals) - len(good)
            vals = list(good)
        axes.append([(name, x) for x in vals])
    out: dict[str, SiteRequest] = {}
    for combo in itertools.product(*axes):
        path = key
        query: list[Param] = []
        for name, v in combo:
            if name in slots and v is not None:
                path = path.replace("{" + name + "}", v)
            elif v is not None and not (name in d and _text(d[name]) == v):
                query.append((name, v))
        req = SiteRequest(path, tuple(sorted(query)))
        out.setdefault(req.file, req)
    return list(out.values()), skipped


def _dump(body: Any) -> bytes:
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _shape(key: str) -> str:
    """시간을 묶을 경로 모양 — 종목 코드·테마 id 를 자리표시로."""
    parts = key.split("/")
    if len(parts) >= 2 and parts[0] in ("stock", "themes") and parts[1] != "search-index":
        parts[1] = "{id}"
    return "/".join("{code}" if CODE_RE.match(x) else x for x in parts)


class _Fetcher:
    def __init__(
        self, client: TestClient, report: SiteReport, log: Callable[[str], None] | None
    ) -> None:
        self.c = client
        self.report = report
        self.files: dict[str, SiteFile] = {}
        self.log = log
        self.n = 0

    def get(self, req: SiteRequest, shape: str | None = None) -> Any | None:
        """200 이면 본문(관심종목 지움), 404 no_data 면 그 본문을 파일로 쓰고 None,
        그 밖은 실패로 센다. `shape` 는 시간을 묶을 경로 모양(펼침 — 키 틀)."""
        if req.file in self.files:
            return None
        t0 = time.monotonic()
        r = self.c.get(req.url)
        name = shape or _shape(req.key)
        self.report.seconds[name] = self.report.seconds.get(name, 0.0) + time.monotonic() - t0
        self.n += 1
        if self.log is not None and self.n % 200 == 0:
            self.log(f"  … 요청 {self.n} · 파일 {self.report.files} · {req.key}")
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code == 200 and isinstance(body, dict):
            clean = scrub(body)
            self.put(req.file, clean)
            return clean
        if r.status_code == 404 and isinstance(body, dict) and body.get("code") == "no_data":  # pyright: ignore[reportUnknownMemberType]
            self.put(req.file, body)
            self.report.no_data += 1
            return None
        self.report.failed[req.file] = r.status_code
        return None

    def put(self, name: str, body: Any) -> None:
        data = _dump(body)
        self.files[name] = SiteFile(name, data)
        self.report.files += 1
        self.report.bytes += len(data)


def _stock_index(app: FastAPI, detail: Collection[str]) -> tuple[dict[str, Any], bool]:
    """전 종목 목록 본문 — (본문, 데이터 있음). 유니버스·DART 표가 비면 404 no_data
    본문(화면이 서버 문구)."""
    st = getattr(app.state, "kbj", None)
    if not isinstance(st, ApiState):
        raise RuntimeError("create_app 으로 만든 앱이 아니다(app.state.kbj 없음)")
    try:
        env = stock_index(st.read, detail)
    except NoData as e:
        return NoDataBody(message=e.message).model_dump(mode="json"), False
    return scrub(env.model_dump(mode="json")), True


def build_site(
    app: FastAPI,
    which: SetName,
    config: SiteConfig,
    now: Callable[[], datetime],
    *,
    client: TestClient | None = None,
    log: Callable[[str], None] | None = None,
) -> tuple[list[SiteFile], SiteReport]:
    """묶음 하나를 만든다 — 파일 목록과 결과(파일 수·크기·아직 없음·실패·경로별 시간).

    `log` 는 진행 줄(요청 200개마다) — 명령줄은 stderr 로 넘긴다."""
    report = SiteReport(which)
    f = _Fetcher(client or audit_client(app), report, log)
    today = kst_date(now())
    defaults = query_defaults(app)
    known = query_names(app)

    def exportable(key: str) -> bool:
        """오타·개인 경로(SKIP) — 부르지 않고 결과에 남긴다(설정 오류를 삼키지 않는다)."""
        if key in known:
            return True
        report.failed[f"{key} (설정 — 내보내는 GET 경로 아님)"] = 404
        return False

    def run_grid(g: SiteGrid, code: str | None = None) -> set[str]:
        """펼침 하나 — 응답에 나온 종목 코드(펼침 하나에 한 번씩 센다)."""
        if not exportable(g.key):
            return set()
        unknown = sorted({n for n, _ in g.params} - set(g.slots) - known[g.key])
        if unknown:
            report.failed[f"{g.key} (설정 — 모르는 인자 {','.join(unknown)})"] = 422
            return set()
        reqs, skipped = grid_requests(g, f.files, today=today, defaults=defaults, code=code)
        if skipped:
            report.skipped[g.key] = report.skipped.get(g.key, 0) + skipped
        found: Counter[str] = Counter()
        for r in reqs:
            body = f.get(r, shape=g.key)
            if body is not None:
                codes_in(body.get("data"), found)
        return set(found)

    if which == "live":
        for k in config.live:
            if exportable(k):
                f.get(SiteRequest(k))
        for g in config.grid:
            if g.key in config.live:
                run_grid(g)
        return list(f.files.values()), report

    seen: Counter[str] = Counter()
    for r in base_requests(app):
        body = f.get(r)
        if body is not None:
            codes_in(body.get("data"), seen)
    # 펼침 — 화면 버튼 조합·필수 인자·경로 인자(값은 설정 + 앞에서 받은 응답)
    for g in config.grid:
        seen.update(run_grid(g))
    # 테마 상세 — 목록에 나온 id 마다
    catalog = f.files.get(site_file("themes"))
    if catalog is not None:
        ids: list[str] = []
        _ids_in(json.loads(catalog.body).get("data"), ids)
        for tid in ids:
            f.get(SiteRequest(f"themes/{tid}"))
    # 종목 상세 — 화면에 나온 종목(많이 나온 순) + always
    codes = list(dict.fromkeys([*config.always, *(c for c, _ in seen.most_common())]))
    templates = code_templates(app)
    detail: list[str] = []
    for code in codes:
        if len(detail) >= config.max_stocks:
            break
        head = f.get(SiteRequest(f"stock/{code}/header"))
        if head is None:
            continue  # 종목 상세가 없다 — 나머지를 부르지 않는다
        detail.append(code)
        for t in templates[1:]:
            f.get(SiteRequest(t.replace("{code}", code)), shape=t)
        for g in config.stock_grid:
            run_grid(g, code)
    report.stocks = len(detail)
    body, has_data = _stock_index(app, detail)
    f.put(f"{SEARCH_INDEX}.json", body)
    if not has_data:
        report.no_data += 1
    defaults_file = _dump(defaults)
    f.files["_defaults.json"] = SiteFile("_defaults.json", defaults_file)
    meta = {"set": which, "generated_at": now().isoformat(), "files": report.files}
    f.files["_meta.json"] = SiteFile("_meta.json", _dump(meta))
    return list(f.files.values()), report


def grid_plan(config: SiteConfig) -> tuple[int, int]:
    """설정의 요청 수 상한 — (full 펼침 조합 수 합, 종목 하나의 펼침 조합 수 합).
    시험·문서용 어림."""
    return sum(g.bound() for g in config.grid), sum(g.bound() for g in config.stock_grid)


# 공개 파일 검사(올리기 전 — 걸리면 아무것도 쓰지 않는다). 개인 경로의 첫 마디, 칸 이름
# (계좌·키·토큰 — 'session'·'session_open' 은 장 세션이라 걸지 않는다), 값(Bearer 머리·KIS 앱키
# 낱말 — 환경 변수 이름만 든 안내 문구는 비밀이 아니라 걸지 않는다)
PRIVATE_DIRS: Final = frozenset(
    p.removeprefix("/api/").split("/")[0] for p in SKIP if p != "/api/stock/search"
)
SECRET_KEY_RE: Final = re.compile(
    r"account|acct|cano|appkey|app_key|secret|token|passw|csrf|cookie", re.I
)
SECRET_VALUE_RE: Final = re.compile(r"\bBearer\s+\S|appsecret|appkey", re.I)
_NAME_RE: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./@-]*\.json$")


def _check_value(v: Any, where: str, out: list[str]) -> None:
    if isinstance(v, dict):
        for k, x in v.items():  # pyright: ignore[reportUnknownVariableType]
            key = str(k)  # pyright: ignore[reportUnknownArgumentType]
            if SECRET_KEY_RE.search(key):
                out.append(f"{where}: 칸 이름 '{key}'")
            if key in WATCH_KEYS and x is not False:
                out.append(f"{where}: 관심종목 표시 '{key}'")
            _check_value(x, where, out)
    elif isinstance(v, list):
        for x in v:  # pyright: ignore[reportUnknownVariableType]
            _check_value(x, where, out)
    elif isinstance(v, str) and SECRET_VALUE_RE.search(v):
        out.append(f"{where}: 비밀 같은 값")


def check_public(files: Iterable[SiteFile]) -> list[str]:
    """공개 사이트에 올리면 안 되는 것 — 빈 목록이면 통과.

    값은 문구에 싣지 않는다(위치·칸 이름만)."""
    out: list[str] = []
    for sf in files:
        name = sf.name
        if ".." in name or not _NAME_RE.match(name):
            out.append(f"{name}: 파일 이름 형식")
            continue
        if name.split("/")[0].split("@")[0].removesuffix(".json") in PRIVATE_DIRS:
            out.append(f"{name}: 개인 경로")
            continue
        try:
            body = json.loads(sf.body)
        except ValueError:
            out.append(f"{name}: JSON 아님")
            continue
        _check_value(body, name, out)
    return out


def _sorted(files: Iterable[SiteFile]) -> Iterator[SiteFile]:
    return iter(sorted(files, key=lambda x: x.name))


def write_tar(files: Iterable[SiteFile], out: io.BufferedIOBase | Any) -> None:
    """tar 스트림(표준 출력 — kbj.sh 가 호스트에서 푼다). 시각은 0 — 같은 내용이면 같은 바이트."""
    with tarfile.open(fileobj=out, mode="w|") as tar:
        for sf in _sorted(files):
            info = tarfile.TarInfo(sf.name)
            info.size = len(sf.body)
            info.mtime = 0
            tar.addfile(info, io.BytesIO(sf.body))


def write_dir(files: Iterable[SiteFile], out: Path) -> None:
    for sf in _sorted(files):
        p = out / sf.name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(sf.body)
