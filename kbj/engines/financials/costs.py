"""정기보고서 주석에서 '비용의 성격별 분류'를 파싱해 7개 버킷으로 접는다.

DART 정형 API에는 이 분해가 없다. 공시원문(document.xml)의 표를 직접 읽는 수밖에 없고,
표 형태가 회사마다 다르므로 config/dart_report/mapping.yaml 의 동의어 목록으로 흡수한다.
매핑이 비면 --diagnose-costs 로 실제 계정명을 뽑아 보강하는 것이 정상 절차다.

KBJ 정본(docs/p4_design.md §1.5·§4.6) — ET `dart-report/dartreport/costs.py`(394줄)를 그대로 옮겼다.
정규식·버킷 순서·표 고르기(기대 총비용 ±5%·파일 하나만)는 같다. 바뀐 것(입출력 경계만)
- 공시 목록·원문은 `DocumentSource` Protocol(`DartClient` 그대로 맞음 — I/O 는 호출자, import-linter
  ⑨).
- `verbose` 출력은 `log` 인자(기본 `print` — legacy CLI 와 같은 화면). kbj 파이프라인은 줄을 모은다.
- `REPRT` 는 쓰지 않는다(원본의 import 만 있던 것 — 지웠다). 매핑 기본 경로는
  `config/dart_report/mapping.yaml`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml
from bs4 import BeautifulSoup

from kbj.config.files import config_path

__all__ = [
    "BUCKET_ORDER",
    "EOK",
    "MAPPING_NAME",
    "REPORT_NAME_TO_Q",
    "CostNote",
    "DocumentSource",
    "Log",
    "annual_cost_structure",
    "bucketize",
    "cost_timeseries",
    "fetch_cost_notes",
    "find_periodic_reports",
    "load_mapping",
    "parse_cost_table",
    "tables_with_title",
]

EOK = 100_000_000
BUCKET_ORDER = ["원가", "인건비", "감가상각비", "지급수수료", "외주용역비", "유지보수비", "기타"]
MAPPING_NAME = "dart_report/mapping.yaml"  # config/ 기준(config/reports.yaml `mapping` 기본값)

Log = Callable[[str], None]

# 정기보고서 보고서명 → 분기
REPORT_NAME_TO_Q = [
    (re.compile(r"분기보고서.*\(20\d{2}\.03"), "1Q"),
    (re.compile(r"반기보고서"), "2Q"),
    (re.compile(r"분기보고서.*\(20\d{2}\.09"), "3Q"),
    (re.compile(r"사업보고서"), "4Q"),
]


class DocumentSource(Protocol):
    """공시 목록(`list.json` 전체 쪽)·공시 원문(`document.xml` zip 안 파일들)."""

    def disclosures(
        self, corp_code: str, bgn_de: str, end_de: str, pblntf_ty: str | None = None
    ) -> list[dict[str, Any]]: ...

    def document_texts(self, rcept_no: str) -> list[tuple[str, str]]: ...


def _emit(verbose: bool, log: Log | None) -> Log | None:
    """verbose 일 때만 내보낸다 — 곳은 `log`(없으면 표준출력 — legacy CLI 와 같은 화면)."""
    if not verbose:
        return None
    return log if log is not None else print


def load_mapping(path: str | Path | None = None) -> dict[str, Any]:
    """매핑 YAML. 경로를 안 주면 `config/dart_report/mapping.yaml`(KBJ_CONFIG_DIR 기준)."""
    p = Path(path) if path is not None else config_path(MAPPING_NAME)
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    return re.sub(r"[\s ,]+", "", s or "")


def _num(s: str) -> float | None:
    t = _norm(s).replace("−", "-").replace("△", "-").replace("▲", "-")
    neg = t.startswith("(") and t.endswith(")")
    if neg:
        t = t[1:-1]
    if not re.fullmatch(r"-?\d+(\.\d+)?", t):
        return None
    v = float(t)
    return -v if neg else v


@dataclass
class CostNote:
    year: int
    quarter: str
    rcept_no: str
    rows: list[tuple[str, float]] = field(default_factory=list[tuple[str, float]])  # (계정명, 원)
    source_title: str = ""
    scale: float = 1.0  # 적용된 단위 배율 (진단 표시용)

    @property
    def label(self) -> str:
        return f"{self.quarter}{str(self.year)[2:]}"


def find_periodic_reports(
    client: DocumentSource, corp_code: str, years: list[int]
) -> dict[tuple[int, str], str]:
    """(연도, 분기) → 정기보고서 rcept_no.

    보고서는 기간 종료 후에 제출되므로 검색 구간을 다음 해 4월까지 넉넉히 잡는다.
    """
    bgn = f"{min(years)}0101"
    end = f"{max(years) + 1}1231"
    rows = client.disclosures(corp_code, bgn, end, pblntf_ty="A")

    out: dict[tuple[int, str], str] = {}
    for r in rows:
        name = r.get("report_nm", "")
        m = re.search(r"\((20\d{2})\.(\d{2})\)", name)
        if not m:
            continue
        period_year, month = int(m.group(1)), m.group(2)
        quarter = {"03": "1Q", "06": "2Q", "09": "3Q", "12": "4Q"}.get(month)
        if not quarter or period_year not in years:
            continue
        # 정정공시가 있으면 나중 접수번호가 최신 — 큰 값으로 덮어쓴다
        key = (period_year, quarter)
        if key not in out or r["rcept_no"] > out[key]:
            out[key] = r["rcept_no"]
    return out


# 표 직전 문맥의 "(단위: 천원)" 류 표기. 늦게(표에 가깝게) 나온 것을 채택한다.
_UNIT_SCALES = [("단위:억원", 1e8), ("단위:백만원", 1e6), ("단위:천원", 1e3), ("단위:원", 1.0)]


def _unit_scale(ctx: str) -> float:
    best_pos, best_scale = -1, 1.0
    for pat, scale in _UNIT_SCALES:
        pos = ctx.rfind(pat)
        if pos > best_pos:
            best_pos, best_scale = pos, scale
    return best_scale


def _table_context(table: Any, limit: int = 1500) -> str:
    """표 직전 텍스트(정규화). 제목·단위 표기가 여기 들어 있다."""
    context = ""
    node = table
    for _ in range(30):
        node = node.find_previous(string=True)
        if node is None:
            break
        context = str(node) + context
        if len(context) > limit:
            break
    return _norm(context)[-limit:]


def _tables_with_title(html: str, title_keywords: list[str]) -> list[tuple[str, Any, float]]:
    """제목 키워드가 표 직전 텍스트에 등장하는 <table> 들을 (키워드, 표, 단위배율) 로 반환."""
    soup = BeautifulSoup(html, "html.parser")
    hits: list[tuple[int, str, Any, float]] = []

    for table in soup.find_all("table"):
        ctx = _table_context(table)
        for rank, kw in enumerate(title_keywords):
            if _norm(kw) in ctx:
                # 단위 표기가 표 안(캡션 셀)에 있는 보고서가 있다 (006110 1Q26 실측).
                # 표 내부가 더 가까우므로 내부 우선, 없으면 직전 문맥.
                inner = _norm(table.get_text(" ", strip=True))
                scale = _unit_scale(inner) if "단위:" in inner else _unit_scale(ctx)
                hits.append((rank, kw, table, scale))
                break

    hits.sort(key=lambda h: h[0])
    return [(kw, t, sc) for _rank, kw, t, sc in hits]


def _strip_footnote(n: str) -> str:
    """'합계(*2)' → '합계'. 각주 표기는 라벨 대조 전에 뗀다."""
    return re.sub(r"(\((?:\*|주)?\d*\))+$", "", n)


def _parse_cost_table(table: Any, scale: float = 1.0) -> list[tuple[str, float]]:
    """표에서 (계정명, 값) 추출. scale = 단위 배율(천원=1e3).

    반기·분기보고서 표는 '3개월 / 누적' 두 열이 실리고 첫 숫자열이 3개월이다
    (006110 실측: 1Q 543억 > 2Q 첫열 481억인데 네 분기 합이 연간과 일치).
    누적 차분 로직은 누적을 전제하므로, 헤더에서 '누적' 열을 찾아 그 열을 읽는다.
    헤더가 없거나 열이 안 맞으면 첫 숫자열로 폴백.
    """
    trs = table.find_all("tr")

    def _cells(tr: Any) -> list[str]:
        return [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th", "te", "tu"])]

    # 헤더(숫자 없는 앞쪽 행들)에서 '누적'/'누계' 열 위치를 찾는다
    cum_idx = header_len = None
    for tr in trs:
        cells = _cells(tr)
        if not cells:
            continue
        if any(_num(c) is not None for c in cells):
            break  # 데이터 시작
        for i, c in enumerate(cells):
            if "누적" in _norm(c) or "누계" in _norm(c):
                cum_idx, header_len = i, len(cells)
                break
        if cum_idx is not None:
            break

    rows: list[tuple[str, float]] = []
    for tr in trs:
        cells = _cells(tr)
        if len(cells) < 2:
            continue
        label = _norm(cells[0])
        if not label or _num(cells[0]) is not None:
            continue  # 첫 칸이 숫자면 라벨 행이 아님
        if len(label) > 40:
            continue  # 계정명이 아니라 캡션 문장이 셀에 들어간 행
        v = None
        if cum_idx is not None:
            # 헤더에 라벨 칸이 있으면(길이 같음) 같은 위치, 없으면(한 칸 짧음) +1
            if len(cells) == header_len:
                v = _num(cells[cum_idx]) if cum_idx < len(cells) else None
            elif header_len is not None and len(cells) == header_len + 1:
                v = _num(cells[cum_idx + 1]) if cum_idx + 1 < len(cells) else None
        if v is None:
            for cell in cells[1:]:
                v = _num(cell)
                if v is not None:
                    break
        if v is not None:
            rows.append((label, v * scale))
    return rows


def _table_total(parsed: list[tuple[str, float]], totals: set[str]) -> float:
    """표의 총비용: 합계행(라벨 또는 rowspan 으로 라벨을 잃은 것)이 있으면 그 값, 없으면 항목 합."""
    for lbl, v in parsed:
        if _strip_footnote(_norm(lbl)) in totals:
            return v
    grand = sum(v for _l, v in parsed)
    for _l, v in parsed[:2]:
        if abs(grand - 2 * v) < 0.005 * max(abs(v), 1.0):
            return v  # 라벨 없는 합계행 — bucketize 의 탐지와 같은 규칙
    return grand


def fetch_cost_notes(
    client: DocumentSource,
    corp_code: str,
    years: list[int],
    mapping: dict[str, Any],
    verbose: bool = False,
    expected: dict[tuple[int, str], float] | None = None,
    *,
    log: Log | None = None,
) -> list[CostNote]:
    """expected: (연도,분기) → 기대 총비용(누적 매출-영업이익, 정형 API 원 단위).

    같은 공시 ZIP 에 연결·별도 주석이 함께 들어 있어, 표제·합계행만으로는
    별도 표를 잡을 수 있다 (005930 실측 — 4Q25 별도 표의 계가 연결 총비용과
    75.6조 차이). 기대값에 가까운 표를 우선한다.
    """
    say = _emit(verbose, log)
    reports = find_periodic_reports(client, corp_code, years)
    titles = mapping.get("note_titles", [])
    totals = {_norm(t) for t in mapping.get("total_labels", [])}
    expected = expected or {}
    notes: list[CostNote] = []

    for (year, quarter), rcept_no in sorted(reports.items()):
        exp = expected.get((year, quarter))

        def _score(
            kw: str, parsed: list[tuple[str, float]], exp: float | None = exp
        ) -> tuple[int, int, bool, int]:
            # ① 기대 총비용과의 근접(연결/별도·단위 오류를 거른다)
            # ② 표제 우선순위 ③ 합계행 보유 ④ 행 수
            rank = titles.index(kw) if kw in titles else len(titles)
            has_total = any(_strip_footnote(lbl) in totals for lbl, _v in parsed)
            if exp:
                rel = abs(_table_total(parsed, totals) - exp) / max(abs(exp), 1.0)
                tier = 2 if rel < 0.05 else (1 if rel < 0.25 else 0)
            else:
                tier = 1
            return (tier, -rank, has_total, len(parsed))

        best: CostNote | None = None
        best_score = None
        docs = client.document_texts(rcept_no)
        for _fname, html in docs:
            for kw, table, scale in _tables_with_title(html, titles):
                parsed = _parse_cost_table(table, scale)
                if len(parsed) < 3:
                    continue
                sc = _score(kw, parsed)
                if best_score is None or sc > best_score:
                    best = CostNote(year, quarter, rcept_no, parsed, kw, scale)
                    best_score = sc
            if (
                best is not None
                and best_score is not None
                and best_score[0] == 2
                and best_score[1] == 0
                and len(best.rows) >= 6
            ):
                break  # 기대값 일치 + 최우선 표제 — 더 볼 것 없다
        if best:
            notes.append(best)
            if say:
                tt = _table_total(best.rows, totals) / EOK
                tail = f" · 합 {tt:,.0f}억" + (f" / 기대 {exp / EOK:,.0f}억" if exp else "")
                say(f"  [주석] {best.label}  '{best.source_title}'  {len(best.rows)}행{tail}")
        elif say:
            say(f"  [주석] {quarter}{str(year)[2:]}  표를 찾지 못함 (rcept {rcept_no})")
            _dump_candidates(docs, log=say)
    return notes


def _dump_candidates(docs: list[tuple[str, str]], limit: int = 6, *, log: Log = print) -> None:
    """표를 못 찾은 보고서에서, '비용' 낱말이 문맥에 있는 표들의 제목 후보를 보여준다.

    원격 러너에서만 실데이터를 볼 수 있으므로(로컬은 DART 접속 불가),
    note_titles 를 무엇으로 넓혀야 하는지 로그만으로 판단하기 위한 장치다.
    """
    shown = 0
    for _fname, html in docs:
        if shown >= limit:
            break
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.find_all("table"):
            ctx = _table_context(table, 300)
            if "비용" not in ctx and "판매비" not in ctx and "원가" not in ctx:
                continue
            nrows = len(table.find_all("tr"))
            log(f"      후보) …{ctx[-70:]} · {nrows}행")
            shown += 1
            if shown >= limit:
                break


def bucketize(
    rows: list[tuple[str, float]], mapping: dict[str, Any]
) -> tuple[dict[str, float], list[tuple[str, float]]]:
    """계정 행들을 7버킷으로 접는다. 반환: (버킷합계, 미매핑행)."""
    buckets = {b: 0.0 for b in BUCKET_ORDER}
    totals = {_norm(t) for t in mapping.get("total_labels", [])}
    unmapped: list[tuple[str, float]] = []

    # 라벨이 붙은 합계행을 먼저 뺀다
    body = [(lb, v) for lb, v in rows if _strip_footnote(_norm(lb)) not in totals]

    # rowspan 이 라벨을 삼켜 합계행이 계정명('성격별비용' 등)으로 읽히는 표가 있다
    # (005930 분기보고서 실측). 라벨과 무관하게 '값 ≈ 나머지 행들의 합'이면 합계행이다.
    # 맨 앞 두 행만 검사한다 — 뒤쪽 행은 우연히 절반 값인 계정(원가 등)일 수 있다.
    grand = sum(v for _l, v in body)
    skip_idx = {
        i for i, (_l, v) in enumerate(body[:2]) if abs(grand - 2 * v) < 0.005 * max(abs(v), 1.0)
    }

    for i, (label, value) in enumerate(body):
        if i in skip_idx:
            continue
        n = _norm(label)
        placed = False
        for bucket, keywords in mapping["buckets"].items():
            if any(_norm(k) in n for k in keywords):
                buckets[bucket] += value
                placed = True
                break
        if not placed:
            buckets["기타"] += value
            unmapped.append((label, value))
    return buckets, unmapped


def cost_timeseries(
    notes: list[CostNote], mapping: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[tuple[str, float]]]:
    """주석(누적) → 분기 단독 비용구조. 반환: (분기행 리스트, 전체 미매핑행)."""
    order = ["1Q", "2Q", "3Q", "4Q"]
    cum: dict[tuple[int, str], dict[str, float]] = {}
    all_unmapped: list[tuple[str, float]] = []

    for note in notes:
        b, un = bucketize(note.rows, mapping)
        cum[(note.year, note.quarter)] = b
        all_unmapped.extend(un)

    out: list[dict[str, Any]] = []
    for (year, quarter), buckets in sorted(cum.items()):
        idx = order.index(quarter)
        prev = cum.get((year, order[idx - 1])) if idx > 0 else None
        row: dict[str, Any] = {
            "year": year,
            "quarter": quarter,
            "label": f"{quarter}{str(year)[2:]}",
        }
        for bucket in BUCKET_ORDER:
            if idx == 0:
                v = buckets.get(bucket, 0.0)
            elif prev is not None:
                v = buckets.get(bucket, 0.0) - prev.get(bucket, 0.0)
            else:
                v = None
            row[bucket] = None if v is None else v / EOK
        vals = [row[b] for b in BUCKET_ORDER if row.get(b) is not None]
        row["비용합계"] = sum(vals) if vals else None
        out.append(row)

    return out, all_unmapped


def annual_cost_structure(notes: list[CostNote], mapping: dict[str, Any]) -> list[dict[str, Any]]:
    """연도별 최신 누적 주석 → 연간(또는 진행중 누적) 비용구조. 이미지 1 용.

    라벨은 statements.to_annual() 과 동일 규칙: 완결 연도는 '2025',
    진행 중이면 '1H26' / '9M26' 처럼 누적 개월을 표시한다.
    """
    order = ["1Q", "2Q", "3Q", "4Q"]
    months = {"1Q": 3, "2Q": 6, "3Q": 9, "4Q": 12}
    by_year: dict[int, list[CostNote]] = {}
    for n in notes:
        by_year.setdefault(n.year, []).append(n)

    out: list[dict[str, Any]] = []
    for year in sorted(by_year):
        last = sorted(by_year[year], key=lambda n: order.index(n.quarter))[-1]
        if last.quarter == "4Q":
            label = str(year)
        elif last.quarter == "2Q":
            label = f"1H{str(year)[2:]}"
        else:
            label = f"{months[last.quarter]}M{str(year)[2:]}"

        buckets, _ = bucketize(last.rows, mapping)
        row: dict[str, Any] = {"year": year, "label": label, "months": months[last.quarter]}
        for bucket in BUCKET_ORDER:
            row[bucket] = buckets.get(bucket, 0.0) / EOK
        row["비용합계"] = sum(row[b] for b in BUCKET_ORDER)
        out.append(row)
    return out


# legacy shim(dart-report `dartreport/costs.py` — run.py `_diagnose_bridge`)이 가져갈 공개 별칭.
# legacy 는 kbj 의 비공개 이름을 import 하지 않는다(scripts/check_canonical.py legacy_private_name)
# — 같은 함수 객체에 공개 이름을 하나 더 붙인다(두 벌이 아니다)
parse_cost_table = _parse_cost_table
tables_with_title = _tables_with_title
