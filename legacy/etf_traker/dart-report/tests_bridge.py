"""KBJ P4 다리 시험 — dartreport.* 는 kbj 정본을 다시 내보낼 뿐이다(docs/p4_design.md §8.1·D-P4-6 ④).

계산·엑셀은 `kbj.engines.financials`·`kbj.reports.dart_excel` 한 곳이다. 이 파일은 legacy 이름이 그
객체 자체인지(복사본이 아닌지), 옛 CLI(run.py)가 옮긴 매핑을 기본값으로 읽는지, Streamlit app.py 가
없어졌는지 본다. 출력 마지막 줄 `bridge ok: 5 modules` (scripts/test_legacy.sh·
tests/unit/reports/test_legacy_shim.py 가 읽는다).
"""

import os
import sys

sys.path.insert(0, ".")

import dartreport.bridge as d_bridge
import dartreport.costs as d_costs
import dartreport.excel as d_excel
import dartreport.orders as d_orders
import dartreport.statements as d_statements
import run

import kbj.engines.financials.bridge as k_bridge
import kbj.engines.financials.costs as k_costs
import kbj.engines.financials.orders as k_orders
import kbj.engines.financials.statements as k_statements
import kbj.reports.dart_excel.excel as k_excel

pairs = [
    (d_statements, k_statements, ["Period", "fetch_periods", "to_quarterly", "to_annual"]),
    (d_costs, k_costs, ["CostNote", "fetch_cost_notes", "bucketize", "cost_timeseries",
                        "annual_cost_structure", "find_periodic_reports", "load_mapping"]),
    (d_bridge, k_bridge, ["build_bridge", "fetch_bridge_details", "waterfall_layout"]),
    (d_orders, k_orders, ["Order", "fetch_orders", "orders_table"]),
    (d_excel, k_excel, ["build_workbook"]),
]
for legacy_mod, kbj_mod, names in pairs:
    for name in names:
        assert getattr(legacy_mod, name) is getattr(kbj_mod, name), (legacy_mod.__name__, name)
# run.py 진단이 쓰는 옛 밑줄 이름 = kbj 공개 별칭(같은 함수 객체)
assert d_costs._parse_cost_table is k_costs.parse_cost_table
assert d_costs._tables_with_title is k_costs.tables_with_title
assert d_bridge._bucketize_bridge is k_bridge.bucketize_bridge

args = run.parse_args(["--stock", "006110"])
assert args.mapping.endswith(os.path.join("config", "dart_report", "mapping.yaml")), args.mapping
assert os.path.isfile(args.mapping)
assert os.path.realpath("config/mapping.yaml") == os.path.realpath(args.mapping)
assert not os.path.exists("app.py")
print("bridge ok:", len(pairs), "modules")
