"""dart-report 골든 공용 — 사례 입력을 읽어 **legacy 와 kbj 에 똑같이** 흘리는 하네스.

docs/p4_design.md §4.6 '골든 3층'·§8.1, D-P4-6~8. 같은 함수가 legacy 모듈(`make_golden.py` —
shim 전 커밋을 `git archive` 로 푼 dart-report)과 kbj 모듈(시험)을 받는다 — 비교하는 두 쪽이
같은 순서·같은 입력으로 돌았다는 것을 코드 하나가 보증한다.

- G1(빌더): `inputs/<사례>.json` — `Period`·주석 행(`CostNote`)·브릿지 세부·수주(`Order`) 등
  **엔진 입력**. 하네스가 `to_quarterly`·`to_annual`·`cost_timeseries`·`annual_cost_structure`·
  `_bucketize_bridge`·`build_bridge`·`waterfall_layout`·`orders_table` → `build_workbook` 을
  부르고, 중간값(JSON)과 엑셀(정규화 — `tests/oracles/xlsx_normalize.py`)을 돌려준다.
- G2(파이프라인): 합성 세계 `p4_world` 의 DART 응답을 가짜 전송층(`WorldDart`)으로 서빙한다.
  legacy 는 `run.main([...])`, kbj 는 `collect_inputs` + `render`. 호출 순서·로그 줄·
  `build_workbook` 입력·엑셀을 비교한다.
- 숫자는 전부 합성(공개 레포 — CLAUDE.md §2). legacy 패키지는 정적 import 하지 않는다(ADR 0003).
"""

from __future__ import annotations

import gzip
import inspect
import io
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx

HERE: Final = Path(__file__).resolve().parent
REPO: Final = HERE.parents[2]
INPUTS: Final = HERE / "inputs"  # G1 사례 입력(사람이 읽는 JSON)
BUILDER: Final = HERE / "builder"  # G1 legacy 산출(정규화 — gzip JSON)
PIPELINE: Final = HERE / "pipeline"  # G2 legacy 산출(정규화 — gzip JSON)
KST: Final = ZoneInfo("Asia/Seoul")
# kbj 쪽 고정 생성 시각(legacy 는 벽시계 — 정규화가 자리표시로 바꾼다)
GENERATED_AT: Final = datetime(2026, 10, 8, 21, 0, tzinfo=KST)
# G1 사례(설계 §4.6 — 8벌)
BUILDER_CASES: Final = (
    "c1_smoke",
    "c2_ofs_mixed",
    "c3_derived_gap",
    "c4_empty_blocks",
    "c5_all_mapped",
    "c6_bridge_residual",
    "c7_negative",
    "c8_partial_labels",
)
# G2 회사(설계 §8.4 — #1 연결·별도 이중 합산 함정, #12 OFS 만·천원)
PIPELINE_COMPANIES: Final = ("994010", "994110")
PIPELINE_YEARS: Final = (2023, 2026)
FAKE_KEY: Final = "dart-golden-fake-key-" + "0" * 19  # 40자 — 길이 검사만 통과하는 가짜


@dataclass(frozen=True)
class Mods:
    """dart-report 모듈 다섯(legacy `dartreport.*` 또는 kbj 승격본)."""

    statements: ModuleType
    costs: ModuleType
    bridge: ModuleType
    orders: ModuleType
    excel: ModuleType


# ── 입출력 ──────────────────────────────────────────────────────────────────────────


def read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=None, separators=(",", ":"))
    if path.suffix == ".gz":
        # mtime=0 — 같은 내용이면 같은 바이트(체크섬 고정)
        with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            gz.write(text.encode("utf-8"))
    else:
        path.write_text(
            json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8"
        )


def load_case(name: str) -> dict[str, Any]:
    return read_json(INPUTS / f"{name}.json")


def load_meta() -> dict[str, Any]:
    return read_json(HERE / "META.json")


def jsonable(obj: Any) -> Any:
    """중간값 → JSON(튜플 → 목록, 날짜 → ISO, dict 키 → 문자열)."""
    if isinstance(obj, Mapping):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [jsonable(v) for v in obj]
    if isinstance(obj, date | datetime):
        return obj.isoformat()
    if isinstance(obj, float | int | str | bool) or obj is None:
        return obj
    if hasattr(obj, "__dataclass_fields__"):
        return {k: jsonable(getattr(obj, k)) for k in obj.__dataclass_fields__}
    raise TypeError(f"JSON 으로 못 바꾼다: {type(obj).__name__}")


# ── G1 — 빌더 사례 ───────────────────────────────────────────────────────────────────


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _smoke_costs(m: Mods, label: str, scale: float) -> dict[str, Any]:
    """DR tests_smoke.py 의 `costs()` 그대로(합성 비용구조 — 7버킷 비율 고정, 0.1 반올림)."""
    r: dict[str, Any] = {"label": label}
    vals = [scale * x for x in (0.62, 0.12, 0.01, 0.09, 0.04, 0.03, 0.03)]
    for b, v in zip(m.costs.BUCKET_ORDER, vals, strict=True):
        r[b] = round(v, 1)
    return r


def build_kwargs(m: Mods, case: Mapping[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    """사례 입력 → `build_workbook` 키워드 인자(+ 중간값 `_extra`)."""
    st = m.statements
    periods = [
        st.Period(
            year=p["year"],
            quarter=p["quarter"],
            fs_div=p["fs_div"],
            cumulative=dict(p["cumulative"]),
            balance=dict(p["balance"]),
        )
        for p in case["periods"]
    ]
    quarters = st.to_quarterly(periods)
    annual = st.to_annual(periods)

    costs = case["costs"]
    unmapped: list[tuple[str, float]] = [tuple(u) for u in case.get("unmapped", [])]  # type: ignore[misc]
    if costs["mode"] == "smoke":
        qcosts = [_smoke_costs(m, q["label"], (q.get("revenue") or 0) * 0.97) for q in quarters]
        acosts = [_smoke_costs(m, a["label"], (a.get("revenue") or 0) * 0.97) for a in annual]
    elif costs["mode"] == "notes":
        notes = [
            m.costs.CostNote(
                n["year"], n["quarter"], n["rcept_no"], [(lb, v) for lb, v in n["rows"]],
                n.get("source_title", ""), n.get("scale", 1.0),
            )
            for n in costs["notes"]
        ]  # fmt: skip
        qcosts, unmapped = m.costs.cost_timeseries(notes, mapping)
        acosts = m.costs.annual_cost_structure(notes, mapping)
    else:
        qcosts, acosts = [], []

    br = case["bridge"]
    target = periods[br["target"]]
    details: dict[str, float] | None
    if br.get("rows") is not None:
        details, _un = m.bridge._bucketize_bridge(  # legacy 이름 그대로(밑줄)
            [(lb, v) for lb, v in br["rows"]], mapping["bridge"]
        )
    else:
        details = br.get("details")
    steps = m.bridge.build_bridge(target, details)
    layout = m.bridge.waterfall_layout(steps)

    if case.get("orders") is None:
        orders = {"total_eok": 0.0, "nipa_eok": 0.0, "nipa_ratio": None, "rows": []}
    else:
        olist = [
            m.orders.Order(
                rcept_no=o["rcept_no"], rcept_dt=o["rcept_dt"], report_nm=o["report_nm"],
                amount=o.get("amount"), counterparty=o.get("counterparty", ""),
                content=o.get("content", ""), start=_date(o.get("start")),
                end=_date(o.get("end")), is_correction=o.get("is_correction", False),
            )
            for o in case["orders"]
        ]  # fmt: skip
        orders = m.orders.orders_table(olist)

    kw: dict[str, Any] = {
        "meta": dict(case["meta"]),
        "annual": annual,
        "annual_costs": acosts,
        "quarters": quarters,
        "qcosts": qcosts,
        "bridge_steps": steps,
        "bridge_label": br["label"],
        "orders": orders,
        "mapping": mapping,
        "unmapped": unmapped,
        "warnings": list(case.get("warnings", [])),
        "_extra": {"layout": layout, "details": details},
    }
    return kw


def call_build(m: Mods, out: str | io.BytesIO, kw: Mapping[str, Any]) -> None:
    """`build_workbook` — kbj 판은 `generated_at` 을 받는다(legacy 는 벽시계)."""
    args = {k: v for k, v in kw.items() if not k.startswith("_")}
    if "generated_at" in inspect.signature(m.excel.build_workbook).parameters:
        args["generated_at"] = GENERATED_AT
    m.excel.build_workbook(out, **args)


def intermediates(kw: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("annual", "annual_costs", "quarters", "qcosts", "bridge_steps", "orders", "unmapped")
    out = {k: jsonable(kw[k]) for k in keys}
    out["layout"] = jsonable(kw["_extra"]["layout"])
    return out


def run_builder_case(m: Mods, name: str, mapping: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    """사례 하나 → (중간값, xlsx 바이트)."""
    case = load_case(name)
    kw = build_kwargs(m, case, mapping)
    buf = io.BytesIO()
    call_build(m, buf, kw)
    return intermediates(kw), buf.getvalue()


# ── G2 — 합성 세계의 가짜 DART ────────────────────────────────────────────────────────


class NoLimit:
    """리미터 자리 — 골든은 시간이 아니라 순서를 본다."""

    def acquire(self, priority: Any, tr_id: str, timeout: float | None = None) -> None:
        return None

    def on_rate_limited(self) -> float:
        return 0.0


@dataclass
class WorldDart:
    """`p4_world` 의 `dart_json`·`dart_bytes` 를 `httpx.MockTransport` 로(시각 `now` 고정)."""

    world: Any
    now: datetime
    calls: list[list[Any]] = field(default_factory=list[list[Any]])

    def transport(self) -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            endpoint = req.url.path.rsplit("/", 1)[-1]
            params = {k: v for k, v in req.url.params.items() if k != "crtfc_key"}
            self.calls.append([endpoint, dict(sorted(params.items()))])
            if endpoint.endswith(".json"):
                body = self.world.dart_json(endpoint, params, now=self.now)
                return httpx.Response(200, json=body)
            raw = self.world.dart_bytes(endpoint, params, now=self.now)
            if raw is None:
                xml = (
                    "<result><status>013</status>"
                    "<message>조회된 데이타가 없습니다.</message></result>"
                )
                return httpx.Response(200, content=xml.encode("utf-8"))
            return httpx.Response(200, content=raw)

        return httpx.MockTransport(handle)

    def client(self, client_cls: Callable[..., Any]) -> Any:
        """kbj `DartClient`(캐시 없음·리미터 없음·예산 없음 — 모든 호출이 전송층에 닿는다)."""
        return client_cls(
            FAKE_KEY, transport=self.transport(), limiter=NoLimit(), sleep=lambda s: None
        )


def world_now() -> datetime:
    from tests.fixtures.synthetic.p4_world import CLOCK

    return CLOCK


def pipeline_log(stdout: str) -> list[str]:
    """legacy `run.main` 표준출력 → 파이프라인 로그 줄.

    끝의 CLI 꼬리('✔ 생성 완료'·경고 되풀이)는 뺀다 — 그 부분은 kbj CLI(`__main__`)의 몫이다.
    """
    lines = stdout.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("✔ 생성 완료"):
            end = i - 1 if i > 0 and lines[i - 1] == "" else i
            return lines[:end]
    return lines
