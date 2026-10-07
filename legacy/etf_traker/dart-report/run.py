#!/usr/bin/env python3
"""종목코드 하나로 DART 재무 리포트(xlsx)를 만든다.

    export DART_API_KEY='...'
    python run.py --stock 006110
    python run.py --stock 006110 --years 2023 2026 --out out/삼아알미늄.xlsx
    python run.py --stock 006110 --diagnose-costs     # 주석 계정명만 훑어보기
"""

from __future__ import annotations

import argparse
import sys
import traceback
from datetime import date
from pathlib import Path

from dartreport.bridge import build_bridge, fetch_bridge_details
from dartreport.client import DartClient
from dartreport.costs import (
    BUCKET_ORDER,
    annual_cost_structure,
    bucketize,
    cost_timeseries,
    fetch_cost_notes,
    load_mapping,
)
from dartreport.excel import build_workbook
from dartreport.orders import fetch_orders, orders_table
from dartreport.statements import fetch_periods, to_annual, to_quarterly


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="DART 재무 리포트 생성기")
    p.add_argument("--stock", required=True, help="종목코드 6자리 (예: 006110)")
    p.add_argument("--years", nargs=2, type=int, metavar=("FROM", "TO"),
                   help="대상 연도 범위. 기본: 최근 4개년")
    p.add_argument("--fs-div", default="CFS", choices=["CFS", "OFS"],
                   help="CFS=연결(기본), OFS=별도")
    p.add_argument("--out", default=None, help="출력 xlsx 경로")
    p.add_argument("--mapping", default="config/mapping.yaml")
    p.add_argument("--cache", default=".cache")
    p.add_argument("--no-orders", action="store_true", help="수주 공시 수집 생략 (빠름)")
    p.add_argument("--diagnose-costs", action="store_true",
                   help="주석에서 발견한 계정명과 매핑 결과만 출력하고 종료")
    p.add_argument("--diagnose-bridge", action="store_true",
                   help="브릿지 대상 보고서의 영업외손익 표를 파일별로 덤프하고 종료")
    p.add_argument("-q", "--quiet", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    verbose = not args.quiet
    warnings: list[str] = []

    mapping = load_mapping(args.mapping)
    client = DartClient(cache_dir=args.cache)

    stock = str(args.stock).zfill(6)
    corp_code, corp_name = client.corp_code(stock)
    if verbose:
        print(f"■ {corp_name} ({stock}) / DART 고유번호 {corp_code}")

    this_year = date.today().year
    if args.years:
        years = list(range(args.years[0], args.years[1] + 1))
    else:
        years = list(range(this_year - 3, this_year + 1))
    if verbose:
        print(f"  대상 연도: {years[0]}–{years[-1]}")

    # ---------------------------------------------------------------- 재무제표
    if verbose:
        print("· 재무제표 수집…")
    periods = fetch_periods(client, corp_code, years, args.fs_div)
    if not periods:
        print(f"[오류] {corp_name} 의 재무제표를 하나도 받지 못했습니다. "
              "연도 범위나 fs_div(CFS/OFS)를 바꿔보세요.", file=sys.stderr)
        return 2
    quarters = to_quarterly(periods)
    annual = to_annual(periods)
    fs_used = ", ".join(sorted({p.fs_div for p in periods}))
    if verbose:
        print(f"  보고서 {len(periods)}건 → 분기 {len(quarters)}개 ({fs_used})")

    missing_q = [q["label"] for q in quarters if q.get("revenue") is None]
    if missing_q:
        warnings.append(f"매출액을 산출하지 못한 분기: {', '.join(missing_q)} (직전 분기 보고서 누락)")

    # ---------------------------------------------------------------- 비용 주석
    if verbose:
        print("· 정기보고서 주석에서 비용 성격별 분류 파싱…")
    # 연결·별도 주석이 한 ZIP 에 섞여 있어, 정형 API 의 (매출-영업이익)을
    # 기대 총비용으로 넘겨 맞는 표를 고르게 한다 (005930 실측 근거)
    expected_costs = {
        (p.year, p.quarter): p.cumulative["revenue"] - p.cumulative["operating_income"]
        for p in periods
        if p.cumulative.get("revenue") is not None
        and p.cumulative.get("operating_income") is not None
    }
    try:
        notes = fetch_cost_notes(client, corp_code, years, mapping, verbose=verbose,
                                 expected=expected_costs)
    except Exception as exc:  # noqa: BLE001
        notes = []
        warnings.append(f"주석 파싱 중 오류: {exc}")
        if verbose:
            traceback.print_exc()

    if args.diagnose_costs:
        return _diagnose(notes, mapping)
    if args.diagnose_bridge:
        return _diagnose_bridge(client, corp_code, mapping, periods)

    qcosts, unmapped = cost_timeseries(notes, mapping)
    acosts = annual_cost_structure(notes, mapping)
    if not notes:
        warnings.append(
            "비용 성격별 주석 표를 찾지 못했습니다. "
            "config/mapping.yaml 의 note_titles 를 회사 보고서 문구에 맞게 보강하세요."
        )
    if unmapped:
        uniq = len({u[0] for u in unmapped})
        warnings.append(f"미매핑 계정 {uniq}종이 '기타'로 흡수됐습니다 — RAW_미매핑계정 시트 확인.")
    if verbose:
        print(f"  주석 {len(notes)}건 → 분기 비용구조 {len(qcosts)}개 / 미매핑 {len(unmapped)}행")

    # 검증 포인트 2 (README): 비용합계로 역산한 영업이익 vs 손익계산서 공시 영업이익.
    # 크게 다르면 주석 표를 잘못 잡은 것이다. 러너 로그가 유일한 눈이라 매 실행 찍는다.
    ac_by_year = {a["year"]: a for a in acosts}
    if verbose and ac_by_year:
        print("· 검증 — 역산 영업이익(매출−비용합계) vs 공시 영업이익")
        for a in annual:
            c = ac_by_year.get(a["year"])
            rev, op = a.get("revenue"), a.get("operating_income")
            if not c or rev is None or op is None or c.get("months") != a.get("months"):
                continue
            implied = rev - c["비용합계"]
            print(f"   {a['label']:>5}  역산 {implied:>8,.1f}억 / 공시 {op:>8,.1f}억 / 차이 {implied - op:+,.1f}억")

    # ---------------------------------------------------------------- 브릿지
    if verbose:
        print("· 이익 브릿지 구성…")
    expected_nonop = {
        (p.year, p.quarter): p.cumulative["pretax_income"] - p.cumulative["operating_income"]
        for p in periods
        if p.cumulative.get("pretax_income") is not None
        and p.cumulative.get("operating_income") is not None
    }
    try:
        details = fetch_bridge_details(client, corp_code, years, mapping, verbose=verbose,
                                       expected=expected_nonop)
    except Exception as exc:  # noqa: BLE001
        details = {}
        warnings.append(f"브릿지 주석 파싱 중 오류: {exc}")

    full_years = [p for p in periods if p.quarter == "4Q"]
    target = full_years[-1] if full_years else periods[-1]
    bridge_steps = build_bridge(target, details.get((target.year, target.quarter)))
    bridge_label = (
        f"{target.year}년" if target.quarter == "4Q"
        else f"{target.year}년 {target.quarter} 누적"
    )
    residual = next((s for s in bridge_steps if s.get("note")), None)
    if residual and abs(residual["value"]) > 5:
        warnings.append(
            f"이익브릿지 잔차가 {residual['value']:.1f}억원입니다 — "
            "config/mapping.yaml 의 bridge 설정을 보강하면 줄어듭니다."
        )

    # ---------------------------------------------------------------- 수주
    if args.no_orders:
        orders = {"total_eok": 0.0, "nipa_eok": 0.0, "nipa_ratio": None, "rows": []}
    else:
        if verbose:
            print("· 수주(공급계약) 공시 수집…")
        try:
            olist = fetch_orders(
                client, corp_code, f"{years[0]}0101", f"{this_year}1231", verbose=verbose
            )
            orders = orders_table(olist)
        except Exception as exc:  # noqa: BLE001
            orders = {"total_eok": 0.0, "nipa_eok": 0.0, "nipa_ratio": None, "rows": []}
            warnings.append(f"수주 공시 수집 중 오류: {exc}")

    # ---------------------------------------------------------------- 엑셀
    out = Path(args.out or f"out/{corp_name}_{stock}_재무리포트.xlsx")
    out.parent.mkdir(parents=True, exist_ok=True)
    build_workbook(
        str(out),
        meta={
            "corp_name": corp_name,
            "stock_code": stock,
            "corp_code": corp_code,
            "fs_div_used": fs_used,
            "period_label": f"{years[0]}–{years[-1]}",
        },
        annual=annual,
        annual_costs=acosts,
        quarters=quarters,
        qcosts=qcosts,
        bridge_steps=bridge_steps,
        bridge_label=bridge_label,
        orders=orders,
        mapping=mapping,
        unmapped=unmapped,
        warnings=warnings,
    )

    print(f"\n✔ 생성 완료: {out}")
    if warnings:
        print("  경고:")
        for w in warnings:
            print(f"   ⚠ {w}")
    return 0


def _diagnose_bridge(client, corp_code, mapping, periods) -> int:
    """브릿지 대상(마지막 완결 연도) 보고서의 영업외손익 표를 파일별로 보여준다.

    잔차가 어디서 새는지(미매핑 계정, 연결·별도 이중합산)를 러너 로그로 판단하기 위한 장치.
    """
    from dartreport.bridge import _bucketize_bridge
    from dartreport.costs import _parse_cost_table, _tables_with_title, find_periodic_reports

    cfg = mapping["bridge"]
    full = [p for p in periods if p.quarter == "4Q"]
    target = full[-1] if full else periods[-1]
    exp = None
    if target.cumulative.get("pretax_income") is not None and target.cumulative.get("operating_income") is not None:
        exp = target.cumulative["pretax_income"] - target.cumulative["operating_income"]
    reports = find_periodic_reports(client, corp_code, [target.year])
    rcept = reports.get((target.year, target.quarter))
    print(f"\n=== 브릿지 진단: {target.year} {target.quarter} (rcept {rcept}) ===")
    if exp is not None:
        print(f"기대 영업외손익(세전이익-영업이익): {exp / 1e8:,.1f}억")
    if not rcept:
        print("대상 보고서를 찾지 못했다.")
        return 1
    for fname, html in client.document_texts(rcept):
        rows = []
        for kw, table, scale in _tables_with_title(html, cfg["note_titles"]):
            parsed = _parse_cost_table(table, scale)
            if parsed:
                rows.append((kw, parsed))
        if not rows:
            continue
        print(f"\n[파일] {fname}")
        merged = []
        for kw, parsed in rows:
            print(f"  표제 '{kw}' · {len(parsed)}행")
            for label, value in parsed:
                print(f"     {label:<36} {value / 1e8:>12,.1f} 억")
            merged.extend(parsed)
        buckets, un = _bucketize_bridge(merged, cfg)
        d = {k: round(v / 1e8, 1) for k, v in buckets.items()}
        print(f"  → 분해 {d}")
        print(f"  → 버킷합 {sum(buckets.values()) / 1e8:,.1f}억 / 미매핑 {len(un)}건 {[u[0] for u in un][:12]}")
    return 0


def _diagnose(notes, mapping) -> int:
    """주석에서 실제로 무슨 계정명이 나오는지 보여준다 — 매핑 튜닝용."""
    if not notes:
        print("주석 표를 하나도 찾지 못했습니다. mapping.yaml 의 note_titles 를 넓혀보세요.")
        return 1
    print(f"\n=== 주석 진단: {len(notes)}개 보고서 ===")
    seen: dict[str, float] = {}
    for note in notes:
        buckets, unmapped = bucketize(note.rows, mapping)
        print(f"\n[{note.label}] 표제 '{note.source_title}' · {len(note.rows)}행 · 배율 {note.scale:g}")
        for label, value in note.rows:
            print(f"     {label:<36} {value / 1e8:>12,.1f} 억")
        for b in BUCKET_ORDER:
            print(f"   {b:<10} {buckets[b] / 1e8:>12,.1f} 억")
        for label, value in unmapped:
            seen[label] = seen.get(label, 0.0) + value
    if seen:
        print("\n--- 미매핑 계정 (전부 '기타'로 감) ---")
        for label, value in sorted(seen.items(), key=lambda kv: -abs(kv[1])):
            print(f"   {label:<40} {value / 1e8:>12,.1f} 억")
        print("\n→ config/mapping.yaml 의 buckets 에 위 계정명을 추가하세요.")
    else:
        print("\n미매핑 계정 없음.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
