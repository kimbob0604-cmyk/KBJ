"""공개 내보내기 본체 — `export_all`(파일 쓰기)과 등록부 처리기 `run`(`public.export`, 05:30).

docs/p3_design.md §7, docs/p4_design.md §1.9·§7, DATA_TIERS §3, ADR 0002 §2.2·0011.

- 읽는 것: `pub_*` 만. DB 는 먼저 연결을 확인한다: 연결이 읽기 전용이고 `kbj_public_export` 역할의
  구성원인지(아니면 실패 — 앱 DSN 으로 대신 붙은 것을 잡는다). P4 는 그 연결로
  `pub_filings`·`pub_fin` 을 읽어(`filings_json`·`fin_json`·`corp_index` — SQL 은 `pub_` 스키마만,
  `tests/unit/public_export/test_sql_schemas.py` 가 정적으로 본다) 공시 목록 다섯·`corp_index.json`·
  `fin/<코드>.json`·`reports/<코드>.{json,xlsx}` 를 만든다.
- **DB 없이(`build --no-db` — CI dry-run) 는 P4 파일을 만들지 않고 manifest 에도 넣지 않는다**(빈
  파일·가짜 값 금지 — 이유는 `Manifest.skipped`·CLI 출력). 표가 비어 있어도 그 파일은 만들지
  않는다.
- `fin/` 합계 크기가 `config/fin.yaml public_export.fin_max_total_mb` 를 넘으면 **실패**(조용히
  자르지 않는다 — 절대 규칙 4, D-P4-17). 넘으면 [결정 필요]: 분기 수 줄이기 또는 상위 N.
- P5(docs/p5_design.md §7): `macro/`·`credit.json`(`_p5_files` — 본문은 MAC). 테마 사전 `themes/` 는
  사용자가 따로 진행해 P5 에서 뺐다.
  크기 상한은 `manifest.DIR_CAPS`·`FILE_CAPS` — 넘으면 같은 식으로 실패.
- P6(docs/p6_design.md §7): `trade/`(`_p6_files` — 본문은 FLASH `trade_flash_json`·EXP
  `trade_explore_json`·MAP `trade_stock_json`). 공개 매핑(`origin = 'public'`)만, 시세 칸 없음.
  상한은 `DIR_CAPS["trade"]`·`FILE_CAPS`·`PREFIX_CAPS`.
- P7(docs/p7_design.md §7): `diag/`(`_p7_files` — 본문은 DIAG `diag_json`). `pub_diag` 만, 로그인
  판정 키(fear_greed·verdict·gex*) 없음(`manifest.diag_problems`). 상한은 `DIR_CAPS["diag"]`·
  `FILE_CAPS`.
- 페이지 9(ADR 0039): 평평한 `themes.json`(`build_themes` — `pub_themes.theme` 대테마 분류만,
  네이버 유래·강도 없음 — `manifest.theme_problems`). 상한은 `FILE_CAPS["themes.json"]`.
- 그 밖에 쓰는 것: 거래 캘린더(코드 계산)·`config/calendar_events.yaml`(공개 설정)·`config/fin.yaml`
  (중요도 등급·크기 상한)·`config/reports.yaml`(공개 데모 종목).
- 이 패키지는 로그인 등급 코드(`kbj.data.private`·`kbj.engines`·`kbj.services.{api,collectors,
  engine}`·`kbj.store.repos`)를 import 하지 않는다(계약 ⑧ — S 가 pyproject 에 넣는다).
- 쓰기 순서: 파일마다 내용 검사(`check_payload`) → 임시 파일 → 이름 바꾸기, 마지막에 manifest.
  검사에 하나라도 걸리면 아무 파일도 바꾸지 않는다. manifest 에 없는 옛 `*.json`(하위 폴더의 옛
  `<코드>.json`·`.xlsx` 포함)은 지운다(남은 파일이 다음 푸시에 섞이지 않게).
- 푸시·디스패치(`push.py`)는 `KBJ_PUBLIC_PUSH_ENABLED`(기본 false — [사용자 승인 필요]) 일 때만.
"""

from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

import psycopg
from pydantic import SecretStr

from kbj.config.fin import FinConfig, ReportsConfig, load_fin, load_reports
from kbj.config.markets import CalendarEvents, load_calendar_events
from kbj.config.settings import Settings
from kbj.core.calendar import TradingCalendar
from kbj.core.masking import mask_text
from kbj.services.public_export.calendar_json import build_calendar
from kbj.services.public_export.corp_index import NAME as CORP_INDEX_NAME
from kbj.services.public_export.corp_index import build_corp_index, load_corps
from kbj.services.public_export.credit_json import build_credit
from kbj.services.public_export.diag_json import build_diag
from kbj.services.public_export.events_json import build_events
from kbj.services.public_export.filings_json import build_filings
from kbj.services.public_export.fin_json import FIN_DIR, build_fin
from kbj.services.public_export.macro_json import build_macro
from kbj.services.public_export.manifest import (
    DART_SUBDIRS,
    DIR_CAPS,
    MANIFEST_NAME,
    NAME_RE,
    SUBDIR_JSON_RE,
    SUBDIR_XLSX_RE,
    SUBDIRS,
    BlobFile,
    DirEntry,
    FileEntry,
    Manifest,
    PublicFile,
    check_payload,
    dir_digest,
    file_cap,
    iso_kst,
    sha256_bytes,
    sha256_text,
    worst_quality,
)
from kbj.services.public_export.pub_read import Reader, sql_reader
from kbj.services.public_export.push import (
    PUBLIC_REPO,
    PushError,
    dispatch_pages,
    push_public_data,
    ssh_remote,
)
from kbj.services.public_export.themes_json import build_themes
from kbj.services.public_export.trade_explore_json import build_trade_explore
from kbj.services.public_export.trade_flash_json import build_trade_flash
from kbj.services.public_export.trade_stock_json import build_trade_stock
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.db import StoreError, connect

__all__ = [
    "EXPORT_ROLE",
    "NOT_PG_SKIP",
    "NO_DB_SKIP",
    "PUBLIC_SUBDIR",
    "PublicExportError",
    "check_public_connection",
    "default_connect",
    "export_all",
    "run",
    "write_files",
]

PUBLIC_SUBDIR: Final = "public"
EXPORT_ROLE: Final = "kbj_public_export"
_SERVICE: Final = "public-export"
# 연결 확인 — 스키마 이름을 쓰지 않는다(이 패키지의 SQL 은 pub_ 만)
_CHECK_SQL: Final = (
    "SELECT current_setting('transaction_read_only'), pg_has_role(current_user, %s, 'MEMBER')"
)

ConnectFn = Callable[[], "psycopg.Connection[Any]"]
NO_DB_SKIP: Final = (
    "P4 공시·재무 파일(공시 목록 다섯·corp_index·fin/·reports/)·P5 매크로 파일(macro/·credit.json):"
    " DB 없이 만들어 넣지 않았다"
)
NOT_PG_SKIP: Final = (
    "P4 공시·재무·P5 매크로 파일: 공개 연결이 psycopg 연결이 아니라(시험 흉내 — 확인만)"
    " 표를 읽지 않았다 — 읽기는 자원 public_reader 로 주입"
)
_MB: Final = 1024 * 1024


class PublicExportError(RuntimeError):
    """공개 내보내기 실패(문구에 값·접속 정보 없음)."""


@dataclass(frozen=True)
class _ExportDsn:
    """`kbj.store.db.connect` 가 읽는 모양 — 공개 내보내기 전용 DSN 을 database_url 자리에."""

    database_url: SecretStr | None


def default_connect(settings: Settings) -> ConnectFn | None:
    """`KBJ_PUBLIC_EXPORT_DATABASE_URL` 연결 공장. 없으면 None — 앱 DSN 으로 대신하지 않는다."""
    url = settings.public_export_database_url
    if url is None:
        return None
    dsn = _ExportDsn(url)

    def _connect() -> psycopg.Connection[Any]:
        try:
            return connect(dsn, service=_SERVICE)
        except StoreError as e:
            # connect 문구는 앱 DSN 이름(KBJ_DATABASE_URL) — 이 연결의 변수 이름으로 바꾼다
            raise PublicExportError(
                str(e).replace("KBJ_DATABASE_URL", "KBJ_PUBLIC_EXPORT_DATABASE_URL")
            ) from None

    return _connect


def check_public_connection(conn: psycopg.Connection[Any]) -> None:
    """읽기 전용 + `kbj_public_export` 구성원인지. 아니면 `PublicExportError`."""
    try:
        with conn.cursor() as cur:
            cur.execute(_CHECK_SQL, (EXPORT_ROLE,))
            row = cur.fetchone()
    except psycopg.Error as e:
        kind = type(e).__name__ + (f"({e.sqlstate})" if e.sqlstate else "")
        raise PublicExportError(f"공개 내보내기 연결 확인 실패: {kind}") from None
    if row is None:
        raise PublicExportError("공개 내보내기 연결 확인: 결과 없음")
    read_only, member = row
    if str(read_only).lower() != "on":
        raise PublicExportError(
            "공개 내보내기 연결이 읽기 전용이 아니다(default_transaction_read_only)"
        )
    if member is not True:
        raise PublicExportError(f"공개 내보내기 연결 역할이 {EXPORT_ROLE} 의 구성원이 아니다")


def _write_atomic(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)


def _breakdown(
    sub: str, rendered: Sequence[tuple[PublicFile, str]], blobs: Sequence[BlobFile]
) -> str:
    """상한을 넘은 폴더의 묶음별 크기(큰 순 5개) — 한 단계 아래 폴더(`hs/`)·평평한 파일은 `파일`.
    어디를 줄일지 고르는 데 쓴다(값은 내지 않고 크기만)."""
    sizes: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    items = [(f.name, len(t.encode("utf-8"))) for f, t in rendered if f.subdir == sub]
    items += [(b.name, len(b.body)) for b in blobs if b.subdir == sub]
    for name, n in items:
        rest = name[len(sub) + 1 :]
        key = rest.split("/", 1)[0] + "/" if "/" in rest else "파일"
        sizes[key] += n
        count[key] += 1
    top = sorted(sizes, key=lambda k: -sizes[k])[:5]
    return " · ".join(f"{k} {count[k]}개 {sizes[k] / _MB:.1f} MB" for k in top)


def _dir_entries(
    rendered: Sequence[tuple[PublicFile, str]], blobs: Sequence[BlobFile]
) -> tuple[DirEntry, ...]:
    """하위 폴더마다 개수·합계 크기·요약 해시·as_of(가장 늦은 것)·quality(가장 나쁜 것)."""
    hashes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    sizes: dict[str, int] = defaultdict(int)
    stamps: dict[str, list[datetime]] = defaultdict(list)
    quals: dict[str, list[str]] = defaultdict(list)
    sources: dict[str, set[str]] = defaultdict(set)
    for f, text in rendered:
        sub = f.subdir
        if sub is None:
            continue
        body = text.encode("utf-8")
        hashes[sub].append((f.name, sha256_bytes(body)))
        sizes[sub] += len(body)
        stamps[sub].append(f.as_of)
        quals[sub].append(f.quality)
        sources[sub].add(f.source)
    for b in blobs:
        hashes[b.subdir].append((b.name, sha256_bytes(b.body)))
        sizes[b.subdir] += len(b.body)
    out: list[DirEntry] = []
    for sub in SUBDIRS:
        if sub not in hashes:
            continue
        if not stamps[sub]:
            raise PublicExportError(f"{sub}/: 봉투 파일 없이 바이너리만 있다")
        out.append(
            DirEntry(
                name=sub,
                count=len(hashes[sub]),
                bytes=sizes[sub],
                digest=dir_digest(hashes[sub]),
                # P4 폴더는 DART 하나, P5 폴더(macro)는 파일 원천들을 쉼표로 이은 것
                source="DART" if sub in DART_SUBDIRS else ",".join(sorted(sources[sub])),
                as_of=iso_kst(max(stamps[sub])),
                quality=worst_quality(quals[sub]),
            )
        )
    return tuple(out)


def _blob_problems(
    blobs: Sequence[BlobFile], rendered: Sequence[tuple[PublicFile, str]]
) -> list[str]:
    """엑셀마다 짝 JSON(`reports/<코드>.json`)이 있고 그 `xlsx_sha256` 이 본문 해시와 같은가."""
    claims = {f.name: f.data.get("xlsx_sha256") for f, _ in rendered if f.subdir is not None}
    out: list[str] = []
    for b in blobs:
        pair = b.name.removesuffix(".xlsx") + ".json"
        if pair not in claims:
            out.append(f"{b.name}: 짝 JSON 이 없다")
        elif claims[pair] != sha256_bytes(b.body):
            out.append(f"{b.name}: 짝 JSON 의 xlsx_sha256 과 다르다")
    return out


def _remove_stale(out_dir: Path, keep_flat: set[str], keep_sub: set[str]) -> None:
    for old in out_dir.glob("*.json"):
        if old.name not in keep_flat and NAME_RE.fullmatch(old.name):
            old.unlink()
    for sub in SUBDIRS:
        root = out_dir / sub
        if not root.is_dir():
            continue
        # P6 trade/ 는 한 단계 더 깊다(trade/hs/<장>.json …) — 아래까지 본다
        for old in sorted(root.rglob("*"), reverse=True):
            rel = old.relative_to(out_dir).as_posix()
            if old.is_dir():
                if not any(old.iterdir()):
                    old.rmdir()
                continue
            ours = bool(SUBDIR_JSON_RE.fullmatch(rel) or SUBDIR_XLSX_RE.fullmatch(rel))
            # 쓰다 끊긴 임시 파일(`_write_atomic` 의 `.<이름>.part`)도 지운다 — 남으면 check_tree 가
            # '허용하지 않는 이름'으로 막아 다음 푸시가 계속 실패한다
            part = old.name.startswith(".") and old.name.endswith(".part")
            if (ours and rel not in keep_sub) or part:
                old.unlink()
        if not any(root.iterdir()):
            root.rmdir()


def write_files(
    files: Sequence[PublicFile],
    out_dir: Path,
    *,
    now: datetime,
    blobs: Sequence[BlobFile] = (),
    caps: Mapping[str, int] | None = None,
    skipped: Sequence[str] = (),
) -> Manifest:
    """봉투 파일들(+ 하위 폴더 바이너리)과 manifest 를 쓴다. 검사에 걸리면 아무것도 쓰지 않고
    `PublicExportError`. `caps` = 하위 폴더 → 합계 바이트 상한(넘으면 실패 — 자르지 않는다)."""
    names = [f.name for f in files] + [b.name for b in blobs]
    if len(set(names)) != len(names):
        raise PublicExportError("같은 이름의 공개 파일이 두 번 있다")
    rendered: list[tuple[PublicFile, str]] = []
    problems: list[str] = []
    for f in files:
        try:
            text = f.render(now)
        except ValueError as e:  # NaN·inf — JSON 에 넣지 않는다
            problems.append(f"{f.name}: JSON 으로 쓸 수 없는 값({e})")
            continue
        problems += check_payload(f.name, text)
        rendered.append((f, text))
    problems += _blob_problems(blobs, rendered)
    if problems:
        raise PublicExportError("공개 산출물 검사 실패: " + "; ".join(problems[:5]))
    for f, text in rendered:
        cap = file_cap(f.name)
        if cap is not None and len(text.encode("utf-8")) > cap:
            raise PublicExportError(
                f"{f.name}: {len(text.encode('utf-8'))} B 가 상한 {cap} B 를 넘는다 — "
                "조용히 자르지 않는다"
            )
    dirs = _dir_entries(rendered, blobs)
    for d in dirs:
        cap = (caps or {}).get(d.name)
        if cap is not None and d.bytes > cap:
            raise PublicExportError(
                f"{d.name}/ 합계 {d.bytes / _MB:.1f} MB 가 상한 {cap / _MB:.1f} MB 를 넘는다"
                f"({_breakdown(d.name, rendered, blobs)}) — "
                "조용히 자르지 않는다([결정 필요]: 분기 수 줄이기 또는 상위 N)"
            )
    entries = tuple(
        FileEntry(
            name=f.name,
            source=f.source,
            as_of=iso_kst(f.as_of),
            quality=f.quality,
            sha256=sha256_text(text),
        )
        for f, text in rendered
        if f.subdir is None
    )
    manifest = Manifest(generated_at=now, files=entries, dirs=dirs, skipped=tuple(skipped))
    manifest_text = manifest.render()
    problems += check_payload(MANIFEST_NAME, manifest_text)
    if problems:
        raise PublicExportError("공개 산출물 검사 실패: " + "; ".join(problems[:5]))
    out_dir.mkdir(parents=True, exist_ok=True)
    for f, text in rendered:
        _write_atomic(out_dir / f.name, text)
    for b in blobs:
        _write_atomic(out_dir / b.name, b.body)
    flat = {f.name for f, _ in rendered if f.subdir is None}
    sub = {f.name for f, _ in rendered if f.subdir is not None} | {b.name for b in blobs}
    _remove_stale(out_dir, flat | {MANIFEST_NAME}, sub)
    _write_atomic(out_dir / MANIFEST_NAME, manifest_text)
    return manifest


def _p4_files(
    read: Reader, *, now: datetime, fin_cfg: FinConfig, reports_cfg: ReportsConfig
) -> tuple[list[PublicFile], list[BlobFile], list[str]]:
    """P4 공시·재무 파일(평평한 여섯 + 하위 폴더) — 만들지 않은 것은 이유와 함께."""
    corps = load_corps(read)
    filings = build_filings(read, corps, now=now, cfg=fin_cfg)
    fin = build_fin(read, corps, now=now, demo=reports_cfg.public_demo)
    files = [*filings.files]
    skipped = [*filings.skipped, *fin.skipped]
    if corps:
        files.append(
            build_corp_index(corps, now, fin_codes=fin.fin_codes, report_codes=fin.report_codes)
        )
    else:
        skipped.append(f"{CORP_INDEX_NAME}: 상장사 표(corp_code)가 비었다 — 만들지 않았다")
    files += fin.files
    return files, list(fin.blobs), skipped


def _p5_files(read: Reader, *, now: datetime) -> tuple[list[PublicFile], list[str]]:
    """P5 공개 파일(macro/·credit.json — pub_macro·pub_market_stats 만).

    본문은 MAC(`macro_json`·`credit_json`). 공개 표가 비면 파일 0·이유 0(docs/p5_design.md §7)."""
    files: list[PublicFile] = []
    skipped: list[str] = []
    for build in (build_macro, build_credit):
        out = build(read, now=now)
        files += out.files
        skipped += out.skipped
    return files, skipped


def _p6_files(read: Reader, *, now: datetime) -> tuple[list[PublicFile], list[str]]:
    """P6 공개 파일(trade/ — pub_trade·pub_fin 만, docs/p6_design.md §7).

    본문은 FLASH(`trade_flash_json`)·EXP(`trade_explore_json`)·MAP(`trade_stock_json`).
    공개 표가 비면 파일 0·이유 0."""
    files: list[PublicFile] = []
    skipped: list[str] = []
    for build in (build_trade_flash, build_trade_explore, build_trade_stock):
        out = build(read, now=now)
        files += out.files
        skipped += out.skipped
    return files, skipped


def _p7_files(read: Reader, *, now: datetime) -> tuple[list[PublicFile], list[str]]:
    """P7 공개 파일(diag/ — pub_diag 만, docs/p7_design.md §7). 본문은 DIAG(`diag_json`)."""
    out = build_diag(read, now=now)
    return list(out.files), list(out.skipped)


def export_all(
    conn_pub: psycopg.Connection[Any] | None,
    out_dir: Path,
    *,
    now: datetime,
    cal: TradingCalendar,
    events: CalendarEvents | None = None,
    settings: Settings | None = None,
    reader: Reader | None = None,
    fin_cfg: FinConfig | None = None,
    reports_cfg: ReportsConfig | None = None,
) -> Manifest:
    """공개 파일 전부를 `out_dir` 에 만든다.

    `conn_pub` 는 공개 내보내기 역할 연결(먼저 확인한다) — 그 연결로 P4 표를 읽는다. None 이면 DB
    없이
    코드 계산·공개 설정 파일만(로컬 빌드·CI — P4 파일은 만들지 않는다). `reader` 는 시험·시뮬레이션
    주입(같은 `Query` 에 같은 뜻으로 답하는 메모리 읽기). 처리기 `run` 은 늘 연결을 준다(운영은
    `kbj.store.db.connect` 의 psycopg 연결 — 확인만 하는 흉내 연결이면 읽지 않고 이유를 남긴다).
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 는 aware datetime")
    if conn_pub is not None:
        check_public_connection(conn_pub)
        if reader is None and isinstance(conn_pub, psycopg.Connection):
            reader = sql_reader(conn_pub)
    evs = events if events is not None else load_calendar_events(settings=settings)
    files: list[PublicFile] = [build_calendar(now, cal), build_events(now, evs)]
    blobs: list[BlobFile] = []
    skipped: list[str] = []
    caps: dict[str, int] = {}
    if reader is None:
        skipped.append(NO_DB_SKIP if conn_pub is None else NOT_PG_SKIP)
    else:
        fcfg = fin_cfg if fin_cfg is not None else load_fin(settings=settings)
        rcfg = reports_cfg if reports_cfg is not None else load_reports(settings=settings)
        p4, blobs, why = _p4_files(reader, now=now, fin_cfg=fcfg, reports_cfg=rcfg)
        files += p4
        skipped += why
        caps[FIN_DIR] = int(fcfg.public_export.fin_max_total_mb * _MB)
        # P5: macro/·credit.json (pub_macro·pub_market_stats)
        p5, why5 = _p5_files(reader, now=now)
        files += p5
        skipped += why5
        # P6: trade/ (pub_trade — 공개 매핑만·시세 칸 없음)
        p6, why6 = _p6_files(reader, now=now)
        files += p6
        skipped += why6
        # P7: diag/ (pub_diag — 공공지표 진단·게이지·이벤트만)
        p7, why7 = _p7_files(reader, now=now)
        files += p7
        skipped += why7
        # 페이지 9: themes.json (pub_themes — 대테마 분류만, ADR 0039)
        p9 = build_themes(reader, now=now)
        files += p9.files
        skipped += p9.skipped
    caps.update(DIR_CAPS)
    return write_files(files, out_dir, now=now, blobs=blobs, caps=caps, skipped=skipped)


def _failed(reason: str, settings: Settings | None, **detail: Any) -> JobResult:
    secrets = settings.secret_values() if settings is not None else []
    return JobResult("failed", detail={**detail, "reason": mask_text(reason, secrets)[:300]})


def run(ctx: JobContext) -> JobResult:
    """등록부 처리기(`kbj.services.public_export:run`).

    자원(선택 — 시험·시뮬레이션이 주입): `public_connect`(연결 공장), `kr`(거래 캘린더),
    `public_out_dir`(산출 디렉터리), `public_remote`(푸시 원격 — 시험은 로컬 bare 저장소),
    `dispatch_client`(httpx.Client — 디스패치 시험용), `public_reader`(P4 표 읽기 — 연결 대신,
    `pub_read.Reader`).
    """
    settings = ctx.settings
    if settings is None:
        return _failed("설정이 주입되지 않았다", None)
    out_dir = Path(ctx.resources.get("public_out_dir") or settings.data_dir / PUBLIC_SUBDIR)
    cal: TradingCalendar = ctx.resources.get("kr") or TradingCalendar.default()
    connect_fn: ConnectFn | None = ctx.resources.get("public_connect") or default_connect(settings)
    if connect_fn is None:
        return _failed(
            "공개 내보내기 DSN(KBJ_PUBLIC_EXPORT_DATABASE_URL)이 없다 — "
            "앱 DSN 으로 대신하지 않는다",
            settings,
        )
    try:
        events = load_calendar_events(settings=settings)
        reader: Reader | None = ctx.resources.get("public_reader")
        with connect_fn() as conn:
            manifest = export_all(
                conn, out_dir, now=ctx.now, cal=cal, events=events, settings=settings,
                reader=reader,
            )  # fmt: skip
    except Exception as e:  # 실행기가 재시도·알림 — 사유는 가린다
        return _failed(f"내보내기: {type(e).__name__}: {e}", settings)
    detail: dict[str, Any] = {
        "files": list(manifest.names),
        "quality": {f.name: f.quality for f in manifest.files},
        "dirs": {d.name: d.count for d in manifest.dirs},
    }
    if manifest.skipped:
        detail["skipped"] = list(manifest.skipped)
    rows = len(manifest.files) + sum(d.count for d in manifest.dirs)
    if not settings.public_push_enabled:
        detail["push"] = "disabled"
        return JobResult("ok", rows=rows, detail=detail)
    key = settings.public_deploy_key_path
    token = settings.github_dispatch_token
    if key is None or token is None:
        return _failed(
            "푸시가 켜졌는데 배포 키 경로 또는 디스패치 토큰이 없다([사용자 승인 필요] 설정)",
            settings,
            **detail,
        )
    try:
        remote = str(ctx.resources.get("public_remote") or ssh_remote(PUBLIC_REPO))
        pushed = push_public_data(
            out_dir, enabled=True, remote=remote, deploy_key_path=key, now=ctx.now
        )
        detail["push"] = pushed.reason
        if pushed.pushed:
            dispatch_pages(token, client=ctx.resources.get("dispatch_client"))
            detail["dispatch"] = "sent"
    except PushError as e:
        return _failed(f"푸시·디스패치: {e}", settings, **detail)
    return JobResult("ok", rows=rows, detail=detail)
