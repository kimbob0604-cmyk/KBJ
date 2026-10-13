"""dart-report 골든 — 합성 입력을 만들고 **legacy dart-report 로** 기대 산출을 캡처한다.

docs/p4_design.md §4.6 '골든 3층'·§8.1, D-P4-6(골든 먼저 → 이식 → 비교 → shim)·D-P4-7(정규화
비교)·D-P4-8(legacy 동작은 버그까지 고정).

쓰는 법(레포 루트)::

    uv run python tests/golden/dart_report/make_golden.py                 # 기본 legacy 커밋에서
    uv run python tests/golden/dart_report/make_golden.py --legacy-commit 607b761

- legacy 는 **shim 전 커밋**의 `legacy/etf_traker/dart-report` 를 `git archive` 로 임시 폴더에
  풀어 그 폴더를 import 루트로 돌린다 — 레포 작업 트리를 바꾸지 않고, shim 뒤에도 같은 legacy 를
  다시 돌릴 수 있다(G3 실데이터 골든도 같은 방식 — 키 수령 뒤 #30).
- G1(빌더 8사례): `inputs/<사례>.json` 을 여기서 만들고(고정 — 난수 없음), legacy 엔진 함수·
  `build_workbook` 을 `golden.build_kwargs` 로 부른다 → `builder/<사례>.json.gz`(중간값 + 정규화
  엑셀 + 수식 수).
- G2(파이프라인 2회사): 합성 세계 `p4_world`(시드 20261007, 시계 2026-11-16 21:30 KST)의 DART
  응답을 가짜 전송층으로 서빙하고 legacy `run.main([...])` 을 그대로 돌린다 →
  `pipeline/<종목>.json.gz`(호출 순서·로그 줄·`build_workbook` 입력·정규화 엑셀).
- 숫자는 전부 합성이다(공개 레포 — CLAUDE.md §2). legacy 패키지는 정적 import 하지 않는다
  (ADR 0003).
- shim 뒤에 이 스크립트를 **작업 트리 legacy 로** 돌리면 kbj 와 자기 자신을 비교하게 된다 —
  그래서 기본값이 `git archive`(커밋 고정)다. 골든 파일은 그 뒤 고치지 않는다
  (`test_golden_meta.py` 가 체크섬을 고정 — 바꿀 일은 새 골든 세트 + ADR).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import io
import subprocess
import sys
import tarfile
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Final

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.golden.dart_report import golden as G  # noqa: E402
from tests.oracles import formula_eval, xlsx_normalize  # noqa: E402

LEGACY_COMMIT: Final = "607b761"  # P3 완료·ADR 0018 커밋 — dart-report 는 P2 재배선 그대로, shim 전
LEGACY_PATH: Final = "legacy/etf_traker/dart-report"
EOK: Final = 100_000_000


# ─────────────────────────── 합성 입력(G1) ───────────────────────────
def _period(y: int, q: str, fs: str, cum: dict[str, Any], bal: dict[str, Any]) -> dict[str, Any]:
    return {"year": y, "quarter": q, "fs_div": fs, "cumulative": cum, "balance": bal}


def case_smoke() -> dict[str, Any]:
    """① DR tests_smoke.py 그대로(14분기·4연도·브릿지 9단계)."""
    labels = [(y, q) for y in range(2023, 2027) for q in ["1Q", "2Q", "3Q", "4Q"]]
    labels = [t for t in labels if not (t[0] == 2026 and t[1] in ("3Q", "4Q"))]
    periods = []
    for y, q in labels:
        n = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}[q]
        base = 300 * n
        cum = {
            "revenue": base * EOK,
            "operating_income": (base * 0.05) * EOK,
            "pretax_income": (base * 0.07) * EOK,
            "tax": (base * 0.015) * EOK,
            "net_income": (base * 0.055) * EOK,
            "cogs": (base * 0.7) * EOK,
        }
        bal = {"total_assets": (2000) * EOK, "total_equity": 1200 * EOK, "cash": 300 * EOK}
        periods.append(_period(y, q, "CFS", cum, bal))
    return {
        "_note": "DR tests_smoke.py 와 같은 합성 데이터(수치·순서 그대로)",
        "meta": {"corp_name": "테스트", "stock_code": "000000", "corp_code": "00000000",
                 "fs_div_used": "CFS", "period_label": "2023–2026"},
        "periods": periods,
        "costs": {"mode": "smoke"},
        "bridge": {"target": -5, "label": "2025년",
                   "details": {"FVPL 평가이익": 59.3 * EOK, "순이자손익": -4.6 * EOK,
                               "순외환손익": -2.3 * EOK, "배당·기타": -1.7 * EOK}},
        "orders": [
            {"rcept_no": "20250820000123", "rcept_dt": "20250820",
             "report_nm": "단일판매·공급계약체결", "amount": 356 * EOK, "counterparty": "카카오",
             "content": "NVIDIA Infiniband 네트워크", "start": "2025-08-20", "end": "2030-12-31"},
            {"rcept_no": "20260703000456", "rcept_dt": "20260703",
             "report_nm": "단일판매·공급계약체결", "amount": 1040 * EOK,
             "counterparty": "네이버클라우드", "content": "2026 NIPA 고성능 AI 스토리지",
             "start": "2026-07-03", "end": "2027-03-31"},
        ],
        "unmapped": [["알수없는수수료", 1.2e8], ["잡비", 3.4e7]],
        "warnings": ["스모크 테스트 실행"],
    }  # fmt: skip


# 분기 단독 손익 비율(매출 대비) — 합성 회사의 결
_Q_SHARE: Final = {"cogs": 0.68, "sgna": 0.2, "fi": 0.012, "fc": 0.018, "oi": 0.004, "oe": 0.006}


def _quarters_of(
    years: dict[int, list[str]],
    rev0: float,
    *,
    growth: float = 0.02,
    opm: dict[tuple[int, str], float] | None = None,
    fs: dict[int, str] | None = None,
    drop: tuple[str, ...] = (),
    zero_rev: tuple[tuple[int, str], ...] = (),
) -> tuple[list[dict[str, Any]], dict[tuple[int, str], dict[str, float]]]:
    """분기 단독 값 → (누적 Period 목록, 분기 단독 dict). 금액은 원(백만원 단위로 반올림)."""
    order = ["1Q", "2Q", "3Q", "4Q"]
    single: dict[tuple[int, str], dict[str, float]] = {}
    i = 0
    for y in sorted(years):
        for q in order:
            rev = 0.0 if (y, q) in zero_rev else round(rev0 * (1 + growth) ** i / 1e6) * 1e6
            i += 1
            m = (opm or {}).get((y, q), 0.08)
            op = round(rev * m / 1e6) * 1e6 if rev else -round(rev0 * 0.05 / 1e6) * 1e6
            cogs = round(rev * _Q_SHARE["cogs"] / 1e6) * 1e6
            fi = round(rev0 * _Q_SHARE["fi"] / 1e6) * 1e6
            fc = round(rev0 * _Q_SHARE["fc"] / 1e6) * 1e6
            oi = round(rev0 * _Q_SHARE["oi"] / 1e6) * 1e6
            oe = round(rev0 * _Q_SHARE["oe"] / 1e6) * 1e6
            pretax = op + fi - fc + oi - oe
            tax = round(pretax * 0.22 / 1e6) * 1e6
            single[(y, q)] = {
                "revenue": rev, "cogs": cogs, "gross_profit": rev - cogs,
                "sgna": rev - cogs - op, "operating_income": op, "finance_income": fi,
                "finance_cost": fc, "other_income": oi, "other_expense": oe,
                "pretax_income": pretax, "tax": tax, "net_income": pretax - tax,
            }  # fmt: skip
    periods: list[dict[str, Any]] = []
    for y in sorted(years):
        cum: dict[str, float] = {}
        for q in order:
            for k, v in single[(y, q)].items():
                cum[k] = cum.get(k, 0.0) + v
            if q not in years[y] or f"{y}{q}" in drop:
                continue
            k_assets = 4.0e12 if rev0 > 1e11 else 1.0e11
            bal = {
                "total_assets": k_assets + 1e9 * len(periods),
                "total_equity": k_assets * 0.55,
                "total_liabilities": k_assets * 0.45 + 1e9 * len(periods),
                "cash": k_assets * 0.08,
            }
            periods.append(_period(y, q, (fs or {}).get(y, "CFS"), dict(cum), bal))
    return periods, single


_COST_SPLIT: Final = (
    ("원재료 사용액", 0.52),
    ("재고자산의 변동", 0.03),
    ("종업원급여", 0.14),
    ("퇴직급여", 0.01),
    ("감가상각비", 0.05),
    ("무형자산상각비", 0.01),
    ("사용권자산상각비", 0.005),
    ("지급수수료", 0.045),
    ("외주가공비", 0.06),
    ("수선비", 0.02),
    ("소모품비", 0.01),
    ("광고선전비", 0.015),
    ("운반비", 0.012),
    ("경상연구개발비", 0.02),  # 매핑에 없다 → '기타' + 미매핑
)


def _notes_of(
    single: dict[tuple[int, str], dict[str, float]],
    keep: list[tuple[int, str]],
    *,
    split: tuple[tuple[str, float], ...] = _COST_SPLIT,
    total_label: str | None = "합계",
    lead_total: str | None = None,
    scale: float = 1e6,
    negative_inventory: bool = False,
) -> list[dict[str, Any]]:
    """분기 단독 총비용(매출 − 영업이익)을 계정으로 나눠 **누적** 주석 행으로(원 단위)."""
    order = ["1Q", "2Q", "3Q", "4Q"]
    notes: list[dict[str, Any]] = []
    for y in sorted({y for y, _ in single}):
        cum: dict[str, float] = {}
        for q in order:
            total = single[(y, q)]["revenue"] - single[(y, q)]["operating_income"]
            used = 0.0
            for i, (label, share) in enumerate(split):
                if i == len(split) - 1:
                    v = total - used
                else:
                    v = round(total * share / 1e6) * 1e6
                    if negative_inventory and label == "재고자산의 변동":
                        v = -abs(v) * 2
                used += v
                cum[label] = cum.get(label, 0.0) + v
            if (y, q) not in keep:
                continue
            rows: list[list[Any]] = [[lb, cum[lb]] for lb, _ in split]
            grand = sum(cum[lb] for lb, _ in split)
            if lead_total is not None:
                rows.insert(0, [lead_total, grand])  # rowspan 이 라벨을 삼킨 합계행(라벨 없음 취급)
            if total_label is not None:
                rows.append([total_label, grand])
            rcept = f"{y}{order.index(q) + 3:02d}15000{len(notes):03d}"
            notes.append({
                "year": y, "quarter": q, "rcept_no": rcept, "rows": rows,
                "source_title": "비용의성격별분류", "scale": scale,
            })  # fmt: skip
    return notes


def _orders_generic() -> list[dict[str, Any]]:
    return [
        {"rcept_no": "20240311000101", "rcept_dt": "20240311",
         "report_nm": "단일판매ㆍ공급계약체결", "amount": 87_500_000_000,
         "counterparty": "합성고객A", "content": "합성 장비 공급",
         "start": "2024-03-11", "end": "2025-02-28"},
        {"rcept_no": "20240402000202", "rcept_dt": "20240402",
         "report_nm": "[기재정정]단일판매ㆍ공급계약체결", "amount": 91_000_000_000,
         "counterparty": "합성고객A", "content": "합성 장비 공급(정정)", "start": "2024-03-11",
         "end": "2025-03-31", "is_correction": True},
        {"rcept_no": "20250915000303", "rcept_dt": "20250915", "report_nm": "공급계약 체결",
         "amount": 12_300_000_000, "counterparty": "합성 NIPA 사업단",
         "content": "합성 AI 서버 납품", "start": "2025-09-15", "end": "2026-06-30"},
    ]  # fmt: skip


_BRIDGE_ROWS: Final = (
    ["이자수익", 1_850_000_000.0],
    ["배당금수익", 120_000_000.0],
    ["외환차익", 960_000_000.0],
    ["외화환산이익", 410_000_000.0],
    [
        "당기손익-공정가치측정금융자산평가이익",
        730_000_000.0,
    ],  # '손' 이 들어 있어 음수로 간다(legacy 버그)
    ["합계", 4_070_000_000.0],
    ["이자비용", 2_640_000_000.0],
    ["외환차손", 1_120_000_000.0],
    ["유형자산처분이익", 95_000_000.0],
    ["유형자산처분손실", 60_000_000.0],
    ["기부금", 45_000_000.0],
    ["잡이익", 33_000_000.0],
    ["기타의 수수료수익", 21_000_000.0],  # 어느 버킷에도 없다 → 잔차로
)


def _meta(name: str, code: str, corp: str, fs: str, label: str) -> dict[str, str]:
    return {"corp_name": name, "stock_code": code, "corp_code": corp, "fs_div_used": fs,
            "period_label": label}  # fmt: skip


def case_ofs_mixed() -> dict[str, Any]:
    """② OFS 폴백·혼재(2022~2023 별도, 2024~2025 연결 — fs_div_used 'CFS, OFS').

    주석 경로(CostNote → 버킷)·각주 붙은 합계행('합계(*2)').
    """
    years = {y: ["1Q", "2Q", "3Q", "4Q"] for y in (2022, 2023, 2024, 2025)}
    periods, single = _quarters_of(years, 2.1e11, fs={2022: "OFS", 2023: "OFS"})
    keep = [(p["year"], p["quarter"]) for p in periods]
    return {
        "_note": "연결 미작성 연도 → OFS 폴백이 섞인 회사. 주석 합계행은 각주 표기('합계(*2)')",
        "meta": _meta("합성기계", "990002", "99000002", "CFS, OFS", "2022–2025"),
        "periods": periods,
        "costs": {"mode": "notes", "notes": _notes_of(single, keep, total_label="합계(*2)")},
        "bridge": {
            "target": len(periods) - 1,
            "label": "2025년",
            "rows": [list(r) for r in _BRIDGE_ROWS],
        },
        "orders": _orders_generic(),
        "warnings": ["미매핑 계정 1종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인."],
    }


def case_derived_gap() -> dict[str, Any]:
    """③ 직전 분기 보고서 없음 → 3Q24 차분 불가(derived, 값 None), 그 분기 비용도 None."""
    years = {2024: ["1Q", "3Q", "4Q"], 2025: ["1Q", "2Q", "3Q", "4Q"]}
    periods, single = _quarters_of(years, 6.4e10)
    keep = [(p["year"], p["quarter"]) for p in periods]
    return {
        "_note": "2024 반기보고서가 없다 — 3Q24 는 직전 누적이 없어 분기 단독을 만들지 않는다",
        "meta": _meta("합성정밀", "990003", "99000003", "CFS", "2024–2025"),
        "periods": periods,
        "costs": {"mode": "notes", "notes": _notes_of(single, keep)},
        "bridge": {"target": len(periods) - 1, "label": "2025년",
                   "details": {"FVPL 평가이익": 3.1e8, "순이자손익": -6.2e8, "순외환손익": 1.4e8,
                               "배당·기타": 0.0}},
        "orders": _orders_generic()[2:],
        "warnings": ["매출액을 산출하지 못한 분기: 3Q24 (직전 분기 보고서 누락)",
                     "미매핑 계정 1종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인."],
    }  # fmt: skip


def case_empty_blocks() -> dict[str, Any]:
    """④ 빈 블록 — 수주 없음·비용 주석 없음·브릿지 데이터 부족(세전·순이익 없음)."""
    years = {2025: ["1Q", "2Q", "3Q", "4Q"]}
    periods, _ = _quarters_of(years, 3.0e10)
    for p in periods:
        for k in ("pretax_income", "net_income", "tax"):
            p["cumulative"].pop(k)
    return {
        "_note": "세 블록이 비는 경우의 안내 문구(데이터 부족·주석 파싱 결과 없음·계약 공시 없음)",
        "meta": _meta("합성서비스", "990004", "99000004", "CFS", "2025–2025"),
        "periods": periods,
        "costs": {"mode": "none"},
        "bridge": {"target": len(periods) - 1, "label": "2025년", "details": None},
        "orders": None,
        "warnings": ["비용 성격별 주석 표를 찾지 못했습니다. config/mapping.yaml 의 note_titles 를 "
                     "회사 보고서 문구에 맞게 보강하세요."],
    }  # fmt: skip


def case_all_mapped() -> dict[str, Any]:
    """⑤ 미매핑 0 — '없음 — 모든 계정이 매핑되었습니다.' + rowspan 이 라벨을 삼킨 합계행(맨 앞)."""
    years = {y: ["1Q", "2Q", "3Q", "4Q"] for y in (2024, 2025)}
    periods, single = _quarters_of(years, 1.5e11, growth=0.01)
    keep = [(p["year"], p["quarter"]) for p in periods]
    split = (*(x for x in _COST_SPLIT if x[0] != "경상연구개발비"), ("기타비용", 0.0))
    return {
        "_note": ("모든 계정이 버킷에 들어간다. 맨 앞 '성격별비용' 행은 값 ≈ 나머지 합이라 "
                  "합계로 빠진다"),
        "meta": _meta("합성화학", "990005", "99000005", "CFS", "2024–2025"),
        "periods": periods,
        "costs": {"mode": "notes",
                  "notes": _notes_of(single, keep, split=split, total_label=None,
                                     lead_total="성격별비용")},
        "bridge": {"target": len(periods) - 1, "label": "2025년",
                   "details": {"FVPL 평가이익": 2.2e9, "순이자손익": -1.9e9, "순외환손익": 4.0e8,
                               "배당·기타": 3.5e8}},
        "orders": _orders_generic()[:1],
        "warnings": [],
    }  # fmt: skip


def case_bridge_residual() -> dict[str, Any]:
    """⑥ 잔차 큰 브릿지 — 주석 세부가 작아 '기타 영업외손익'(잔차) 행이 크다 + 경고."""
    years = {y: ["1Q", "2Q", "3Q", "4Q"] for y in (2024, 2025)}
    periods, single = _quarters_of(years, 9.0e10)
    last = periods[-1]["cumulative"]
    last["pretax_income"] = last["operating_income"] + 8.5e9  # 영업외 85억 — 세부는 그 일부만
    last["net_income"] = last["pretax_income"] - last["tax"]
    keep = [(p["year"], p["quarter"]) for p in periods]
    return {
        "_note": ("세부(FVPL 0.005억 — 0.01억 미만이라 생략)가 작아 잔차가 크다 → "
                  "잔차 행 강조·경고"),
        "meta": _meta("합성에너지", "990006", "99000006", "CFS", "2024–2025"),
        "periods": periods,
        "costs": {"mode": "notes", "notes": _notes_of(single, keep)},
        "bridge": {"target": len(periods) - 1, "label": "2025년",
                   "details": {"FVPL 평가이익": 5.0e5, "순이자손익": -3.2e8, "순외환손익": 1.1e8}},
        "orders": _orders_generic(),
        "warnings": ["미매핑 계정 1종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인.",
                     "이익브릿지 잔차가 85.0억원입니다 — config/mapping.yaml 의 bridge "
                     "설정을 보강하면 줄어듭니다."],
    }  # fmt: skip


def case_negative() -> dict[str, Any]:
    """⑦ 음수·적자 — 영업손실·순손실·법인세 수익(음수)·매출 0 분기·음수 비용(재고 변동).

    수주: 기간 없는 계약·종료일이 시작일보다 앞·금액 없음.
    """
    years = {y: ["1Q", "2Q", "3Q", "4Q"] for y in (2024, 2025)}
    opm = {(2024, "3Q"): -0.12, (2024, "4Q"): -0.3, (2025, "1Q"): -0.05, (2025, "2Q"): 0.02}
    periods, single = _quarters_of(years, 2.4e10, opm=opm, zero_rev=((2025, "3Q"),))
    keep = [(p["year"], p["quarter"]) for p in periods]
    orders = [*_orders_generic()[:1],
        {"rcept_no": "20250105000404", "rcept_dt": "20250105",
         "report_nm": "단일판매ㆍ공급계약체결", "amount": 5_000_000_000,
         "counterparty": "합성고객B", "content": "기간 미기재 계약"},
        {"rcept_no": "20250210000505", "rcept_dt": "20250210",
         "report_nm": "단일판매ㆍ공급계약체결", "amount": 3_300_000_000,
         "counterparty": "합성고객C", "content": "종료일이 시작일보다 앞",
         "start": "2025-06-30", "end": "2025-01-31"},
        {"rcept_no": "20250301000606", "rcept_dt": "20250301",
         "report_nm": "단일판매ㆍ공급계약체결", "amount": None,
         "counterparty": "합성고객D", "content": "금액 없음(표에서 빠진다)"},
    ]  # fmt: skip
    return {
        "_note": "적자 분기·매출 0 분기(OPM 빈칸)·음수 비용·법인세 수익 — 수주 사례 셋",
        "meta": _meta("합성바이오", "990007", "99000007", "CFS", "2024–2025"),
        "periods": periods,
        "costs": {"mode": "notes", "notes": _notes_of(single, keep, negative_inventory=True)},
        "bridge": {
            "target": len(periods) - 1,
            "label": "2025년",
            "rows": [list(r) for r in _BRIDGE_ROWS],
        },
        "orders": orders,
        "warnings": ["미매핑 계정 1종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인."],
    }


def case_partial_labels() -> dict[str, Any]:
    """⑧ 진행 연도 라벨 — 3M22·1H23·2024·2025·9M26(연간 표·연간 비용 구조 라벨 규칙)."""
    years = {2022: ["1Q"], 2023: ["1Q", "2Q"], 2024: ["1Q", "2Q", "3Q", "4Q"],
             2025: ["1Q", "2Q", "3Q", "4Q"], 2026: ["1Q", "2Q", "3Q"]}  # fmt: skip
    periods, single = _quarters_of(years, 4.4e11, growth=0.015)
    keep = [(p["year"], p["quarter"]) for p in periods]
    target = next(i for i, p in enumerate(periods) if (p["year"], p["quarter"]) == (2025, "4Q"))
    return {
        "_note": "부분 연도: 1Q 만(3M)·반기까지(1H)·3분기까지(9M). 브릿지는 마지막 완결 연도",
        "meta": _meta("합성유통", "990008", "99000008", "CFS", "2022–2026"),
        "periods": periods,
        "costs": {"mode": "notes", "notes": _notes_of(single, keep)},
        "bridge": {"target": target, "label": "2025년",
                   "details": {"FVPL 평가이익": 1.2e9, "순이자손익": -2.4e9, "순외환손익": -8.0e8,
                               "배당·기타": 6.0e8}},
        "orders": _orders_generic(),
        "warnings": ["미매핑 계정 1종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인."],
    }  # fmt: skip


CASE_MAKERS: Final = {
    "c1_smoke": case_smoke,
    "c2_ofs_mixed": case_ofs_mixed,
    "c3_derived_gap": case_derived_gap,
    "c4_empty_blocks": case_empty_blocks,
    "c5_all_mapped": case_all_mapped,
    "c6_bridge_residual": case_bridge_residual,
    "c7_negative": case_negative,
    "c8_partial_labels": case_partial_labels,
}


# ─────────────────────────── legacy 실행 ───────────────────────────
def extract_legacy(commit: str, into: Path) -> Path:
    """`git archive <commit> legacy/etf_traker/dart-report` → into/… (레포를 바꾸지 않는다)."""
    raw = subprocess.run(  # noqa: S603 — 고정 인자(git), 사용자 입력 없음
        ["git", "-C", str(REPO), "archive", "--format=tar", commit, LEGACY_PATH],  # noqa: S607
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        tf.extractall(into, filter="data")
    return into / LEGACY_PATH


def legacy_mods(legacy_dir: Path) -> G.Mods:
    sys.path.insert(0, str(legacy_dir))
    for name in list(sys.modules):
        if name == "dartreport" or name.startswith("dartreport.") or name == "run":
            del sys.modules[name]
    imp = importlib.import_module
    return G.Mods(
        statements=imp("dartreport.statements"),
        costs=imp("dartreport.costs"),
        bridge=imp("dartreport.bridge"),
        orders=imp("dartreport.orders"),
        excel=imp("dartreport.excel"),
    )


def capture_builder(mods: G.Mods, mapping: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, maker in CASE_MAKERS.items():
        G.write_json(
            G.INPUTS / f"{name}.json", {"_source": "SYNTHETIC — make_golden.py", **maker()}
        )
        inter, xlsx = G.run_builder_case(mods, name, mapping)
        n = formula_eval.formula_count(xlsx)
        errs = formula_eval.errors(formula_eval.evaluate(xlsx))
        if errs:
            raise SystemExit(f"{name}: legacy 엑셀에 수식 오류 {errs[:5]}")
        G.write_json(G.BUILDER / f"{name}.json.gz", {
            "intermediate": inter, "workbook": xlsx_normalize.normalize(xlsx), "formulas": n,
        })  # fmt: skip
        counts[name] = n
        print(f"  G1 {name}: 수식 {n}개, 시트 {len(xlsx_normalize.normalize(xlsx)['sheets'])}")
    return counts


class _FixedDate(date):
    """legacy run.main 의 `date.today()`(연도 기본값·수주 기간 끝) — 세계 시계의 날짜로."""

    @classmethod
    def today(cls) -> _FixedDate:
        now = G.world_now()
        return cls(now.year, now.month, now.day)


def capture_pipeline(legacy_dir: Path, work: Path) -> dict[str, int]:
    from kbj.data.public.dart.client import DartClient
    from tests.fixtures.synthetic.p4_world import build_world

    world = build_world()
    run_mod: Any = importlib.import_module("run")
    orig_build = run_mod.build_workbook
    counts: dict[str, int] = {}
    for stock in G.PIPELINE_COMPANIES:
        wd = G.WorldDart(world, G.world_now())
        captured: dict[str, Any] = {}

        def _build(path: str, _cap: dict[str, Any] = captured, **kw: Any) -> str:
            _cap.update(kw)
            return orig_build(path, **kw)

        run_mod.DartClient = lambda *a, _wd=wd, **k: _wd.client(DartClient)
        run_mod.date = _FixedDate
        run_mod.build_workbook = _build
        out = work / f"{stock}.xlsx"
        so, se = io.StringIO(), io.StringIO()
        y0, y1 = G.PIPELINE_YEARS
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            rc = run_mod.main([
                "--stock", stock, "--years", str(y0), str(y1), "--out", str(out),
                "--mapping", str(legacy_dir / "config" / "mapping.yaml"),
                "--cache", str(work / "cache"),
            ])  # fmt: skip
        if rc != 0:
            raise SystemExit(f"{stock}: legacy run.main 실패 rc={rc}\n{se.getvalue()}")
        xlsx = out.read_bytes()
        n = formula_eval.formula_count(xlsx)
        errs = formula_eval.errors(formula_eval.evaluate(xlsx))
        if errs:
            raise SystemExit(f"{stock}: legacy 엑셀에 수식 오류 {errs[:5]}")
        inputs = {k: G.jsonable(v) for k, v in captured.items() if k != "mapping"}
        stdout = so.getvalue().replace(str(out), "<out>")  # 임시 폴더 이름은 실행마다 다르다
        G.write_json(G.PIPELINE / f"{stock}.json.gz", {
            "stock": stock, "years": list(G.PIPELINE_YEARS), "rc": rc,
            "stdout": stdout.splitlines(), "stderr": se.getvalue().splitlines(),
            "log": G.pipeline_log(stdout), "calls": wd.calls, "inputs": inputs,
            "workbook": xlsx_normalize.normalize(xlsx), "formulas": n,
        })  # fmt: skip
        counts[stock] = n
        print(f"  G2 {stock}: 호출 {len(wd.calls)}건, 수식 {n}개, 경고 {len(captured['warnings'])}")
    return counts


def checksums() -> dict[str, str]:
    files = sorted(
        [*G.INPUTS.glob("*.json"), *G.BUILDER.glob("*.json.gz"), *G.PIPELINE.glob("*.json.gz")]
    )
    return {str(p.relative_to(G.HERE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="dart-report 골든 캡처(legacy — shim 전 커밋)")
    ap.add_argument("--legacy-commit", default=LEGACY_COMMIT)
    args = ap.parse_args(argv)

    import openpyxl

    with tempfile.TemporaryDirectory() as tmp:
        legacy_dir = extract_legacy(args.legacy_commit, Path(tmp) / "src")
        mods = legacy_mods(legacy_dir)
        mapping = mods.costs.load_mapping(legacy_dir / "config" / "mapping.yaml")
        print(f"legacy dart-report @ {args.legacy_commit} → {legacy_dir}")
        g1 = capture_builder(mods, mapping)
        work = Path(tmp) / "work"
        work.mkdir()
        g2 = capture_pipeline(legacy_dir, work)
        mapping_sha = hashlib.sha256(
            (legacy_dir / "config" / "mapping.yaml").read_bytes()
        ).hexdigest()

    G.write_json(G.HERE / "META.json", {
        "legacy_commit": args.legacy_commit,
        "legacy_source": f"git archive {args.legacy_commit} {LEGACY_PATH} (shim 전 — D-P4-6 ①)",
        "openpyxl": openpyxl.__version__,
        "mapping_sha256": mapping_sha,
        "builder_cases": list(G.BUILDER_CASES),
        "pipeline": {"companies": list(G.PIPELINE_COMPANIES), "years": list(G.PIPELINE_YEARS),
                     "world_seed": 20261007, "world_clock": G.world_now().isoformat()},
        "formula_counts": {**g1, **g2},
        "kbj_generated_at": G.GENERATED_AT.isoformat(),
        "compare": {"rel_tol": xlsx_normalize.REL_TOL, "abs_tol": xlsx_normalize.ABS_TOL,
                    "ignore": ["docProps/*", "요약!'생성 시각' 값"]},
        "checksums": checksums(),
    })  # fmt: skip
    print("META.json 갱신")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
