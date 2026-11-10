"""공개 사이트 내보내기 — Beom's Dashboard 방식(사용자 결정 2026-11-09,
docs/adr/0042-site-snapshot.md).

로그인 화면의 페이지 1~10 이 부르는 GET API 응답을 앱 안에서 한 번씩 불러(audit 와 같은 세션
우회 — 서버·쿠키를 건드리지 않는다) 정적 JSON 파일로 쓴다. 맥이 만들고 GitHub Pages(kbj-site
저장소)가 내보낸다(`scripts/local/kbj.sh site`). 맥이 꺼져 있어도 마지막으로 올린 화면은 열린다.

- 개인 기능(11 규칙·12 백테스트·저널·포트폴리오·13 운영)·관심종목·인증은 내보내지 않는다
  (`SKIP`). 응답 안의 관심종목 표시(`is_watch`)는 false 로 지운다 — 무엇을 보는지가 드러나지 않게.
- 파일 이름(`site_file`)은 web/src/data/snapshot.ts 의 siteFile 과 같은 규칙이다: `<경로>.json`,
  쿼리가 있으면 `<경로>@<키>-<값>.<키>-<값>.json`(키 정렬·빈 값 생략·[A-Za-z0-9_-] 밖 문자는 '_').
- 404 `no_data` 본문도 파일로 쓴다 — 화면이 서버 안내 문구('아직 없음 — 작업 …')를 그대로
  보인다. 그 밖의 4xx·5xx 는 쓰지 않고 결과에 센다(에러를 삼키지 않는다 — 결과 줄에 남긴다).
- 묶음: `live`(장중 10분 — config/site.yaml 의 live 목록만), `full`(경로 전부 + 화면에 나온
  종목의 상세 + 테마 상세 + 검색 목록).
- 종목: 내보낸 응답에 나온 종목 코드(`code` 칸)를 많이 나온 순으로 `max_stocks` 개 + `always`.
  종목 상세가 404 인 코드(ETF 등)는 나머지 상세를 부르지 않는다.
"""

from __future__ import annotations

import io
import json
import re
import tarfile
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlencode

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kbj.services.api.audit import audit_client

__all__ = [
    "SKIP",
    "SiteConfig",
    "SiteExpand",
    "SiteFile",
    "SiteReport",
    "SiteRequest",
    "base_requests",
    "build_site",
    "check_public",
    "codes_in",
    "expand_requests",
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

    def line(self) -> str:
        bad = f" · 실패 {len(self.failed)}" if self.failed else ""
        return (
            f"site {self.set}: 파일 {self.files}개 · {self.bytes / 1e6:.1f} MB · 아직 없음 "
            f"{self.no_data} · 종목 {self.stocks} · {sum(self.seconds.values()):.0f}초{bad}"
        )

    def slowest(self, n: int = 8) -> list[str]:
        top = sorted(self.seconds.items(), key=lambda kv: -kv[1])[:n]
        return [f"{k} {v:.0f}초" for k, v in top]


@dataclass(frozen=True, slots=True)
class SiteExpand:
    """인자 하나를 여러 값으로 펼쳐 부른다 — 필수 인자가 있는 경로(diag/history?key=…)·
    화면의 기준 버튼.

    값은 `values` + 다른 응답(`source` 경로의 data → `list` 목록 → 항목의 `field` 칸)에서 모은 것.
    `fixed` 는 늘 붙이는 인자(화면이 기본값 밖으로 고정해 부르는 것).
    """

    key: str
    param: str
    values: tuple[str, ...] = ()
    source: str | None = None
    list: str = ""
    field: str = ""
    fixed: tuple[Param, ...] = ()


@dataclass(frozen=True, slots=True)
class SiteConfig:
    max_stocks: int = 400
    always: tuple[str, ...] = ()
    live: tuple[str, ...] = ()
    variants: tuple[SiteRequest, ...] = ()
    expand: tuple[SiteExpand, ...] = ()


def _params(raw: object) -> tuple[Param, ...]:
    if not isinstance(raw, Mapping):
        return ()
    items: list[Param] = []
    for k, v in raw.items():  # pyright: ignore[reportUnknownVariableType]
        if v is None or v == "":
            continue
        items.append((str(k), _text(v)))  # pyright: ignore[reportUnknownArgumentType]
    return tuple(sorted(items))


def load_site_config(path: Path) -> SiteConfig:
    """config/site.yaml — 없으면 기본값(묶음 live 는 비어 있다)."""
    if not path.exists():
        return SiteConfig()
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: 최상위가 표가 아니다")
    always = tuple(str(c) for c in raw.get("always") or ())
    bad = [c for c in always if not CODE_RE.match(c)]
    if bad:
        raise ValueError(f"{path.name}: always 에 종목 코드가 아닌 값 {bad}")
    variants: list[SiteRequest] = []
    for v in raw.get("variants") or ():
        if not isinstance(v, dict) or not isinstance(v.get("key"), str):
            raise ValueError(f"{path.name}: variants 항목은 {{key, params}}")
        variants.append(SiteRequest(str(v["key"]), _params(v.get("params"))))  # pyright: ignore[reportUnknownArgumentType]
    expand: list[SiteExpand] = []
    for e in raw.get("expand") or ():
        if not isinstance(e, dict) or not isinstance(e.get("key"), str) or not e.get("param"):
            raise ValueError(f"{path.name}: expand 항목은 {{key, param, values|source}}")
        values = tuple(str(v) for v in e.get("values") or ())  # pyright: ignore[reportUnknownVariableType,reportUnknownArgumentType]
        source = e.get("source")
        if not values and not source:
            raise ValueError(f"{path.name}: expand {e['key']} — values 나 source 가 필요하다")
        expand.append(
            SiteExpand(
                key=str(e["key"]),
                param=str(e["param"]),
                values=values,
                source=None if source is None else str(source),  # pyright: ignore[reportUnknownArgumentType]
                list=str(e.get("list") or ""),  # pyright: ignore[reportUnknownArgumentType]
                field=str(e.get("field") or ""),  # pyright: ignore[reportUnknownArgumentType]
                fixed=_params(e.get("fixed")),
            )
        )
    return SiteConfig(
        max_stocks=int(raw.get("max_stocks", 400)),
        always=always,
        live=tuple(str(k) for k in raw.get("live") or ()),
        variants=tuple(variants),
        expand=tuple(expand),
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
    (diag/history?key= 등)는 빼고 설정의 `expand` 로만 부른다."""
    return [
        SiteRequest(p.removeprefix("/api/"))
        for p in _get_paths(app)
        if "{" not in p and not _required_query(app, p)
    ]


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

    화면은 기본값과 같은 인자를 빼고 파일을 찾는다(snapshot.ts) — 내보내기는 인자 없이 부른다.
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


def expand_requests(e: SiteExpand, files: Mapping[str, SiteFile]) -> list[SiteRequest]:
    values = list(e.values)
    if e.source is not None:
        src = files.get(site_file(e.source))
        if src is not None:
            values += [v for v in harvest(json.loads(src.body), e.list, e.field) if v not in values]
    return [SiteRequest(e.key, tuple(sorted((*e.fixed, (e.param, v))))) for v in values]


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

    def get(self, req: SiteRequest) -> Any | None:
        """200 이면 본문(관심종목 지움), 404 no_data 면 그 본문을 파일로 쓰고 None,
        그 밖은 실패로 센다."""
        if req.file in self.files:
            return None
        t0 = time.monotonic()
        r = self.c.get(req.url)
        shape = _shape(req.key)
        self.report.seconds[shape] = self.report.seconds.get(shape, 0.0) + time.monotonic() - t0
        self.n += 1
        if self.log is not None and self.n % 200 == 0:
            self.log(f"  … 요청 {self.n} · 파일 {self.report.files} · {req.key}")
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code == 200 and isinstance(body, dict):
            clean = scrub(body)
            self._put(req.file, clean)
            return clean
        if r.status_code == 404 and isinstance(body, dict) and body.get("code") == "no_data":  # pyright: ignore[reportUnknownMemberType]
            self._put(req.file, body)
            self.report.no_data += 1
            return None
        self.report.failed[req.file] = r.status_code
        return None

    def _put(self, name: str, body: Any) -> None:
        data = _dump(body)
        self.files[name] = SiteFile(name, data)
        self.report.files += 1
        self.report.bytes += len(data)


def _search_index(headers: Iterable[Mapping[str, Any]], now: datetime) -> dict[str, Any]:
    items = [
        {
            "code": h.get("code"),
            "name": h.get("name"),
            "market": h.get("market"),
            "kind": h.get("kind"),
            "corp_code": h.get("corp_code"),
            "is_watch": False,
        }
        for h in headers
    ]
    t = now.isoformat()
    return {
        "source": "KBJ(공개 사이트 종목 목록)",
        "as_of": t,
        "quality": "ok",
        "notes": ["공개 사이트에 상세가 있는 종목만"],
        "generated_at": t,
        "data": {"q": "", "n_total": len(items), "items": items},
    }


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
    if which == "live":
        reqs = [SiteRequest(k) for k in config.live]
        reqs += [v for v in config.variants if v.key in config.live]
        for r in reqs:
            f.get(r)
        return list(f.files.values()), report

    seen: Counter[str] = Counter()
    for r in [*base_requests(app), *config.variants]:
        body = f.get(r)
        if body is not None:
            codes_in(body.get("data"), seen)
    # 펼침 — 필수 인자·화면의 기준 버튼(값은 설정 + 앞에서 받은 응답)
    for e in config.expand:
        for r in expand_requests(e, f.files):
            body = f.get(r)
            if body is not None:
                codes_in(body.get("data"), seen)
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
    headers: list[Mapping[str, Any]] = []
    for code in codes:
        if len(headers) >= config.max_stocks:
            break
        head = f.get(SiteRequest(f"stock/{code}/header"))
        if head is None:
            continue  # 종목 상세가 없다(ETF 등) — 나머지를 부르지 않는다
        data = head.get("data")
        if isinstance(data, dict):
            headers.append(data)  # pyright: ignore[reportUnknownArgumentType]
        for t in templates[1:]:
            f.get(SiteRequest(t.replace("{code}", code)))
    report.stocks = len(headers)
    index = _dump(_search_index(headers, now()))
    f.files[f"{SEARCH_INDEX}.json"] = SiteFile(f"{SEARCH_INDEX}.json", index)
    report.files += 1
    report.bytes += len(index)
    defaults = _dump(query_defaults(app))
    f.files["_defaults.json"] = SiteFile("_defaults.json", defaults)
    meta = {"set": which, "generated_at": now().isoformat(), "files": report.files}
    f.files["_meta.json"] = SiteFile("_meta.json", _dump(meta))
    return list(f.files.values()), report


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
