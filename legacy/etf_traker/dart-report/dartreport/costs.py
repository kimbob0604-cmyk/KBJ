"""정기보고서 주석 '비용의 성격별 분류' 파싱 — KBJ P4: KBJ 정본을 다시 내보낸다(두 벌 금지).

정본은 `kbj.engines.financials.costs`(함수 본문 그대로 — docs/p4_design.md D-P4-6 ④). 바뀐 것:
원문·공시 목록은 `disclosures`·`document_texts` 를 가진 아무 객체(legacy DartClient 그대로 맞다),
`verbose` 출력은 그대로 표준출력. 매핑 파일은 `config/dart_report/mapping.yaml`(레포 설정 폴더)로
옮겼다 — 이 폴더의 `config/mapping.yaml` 은 그 파일을 가리키는 심볼릭 링크다(tests_smoke.py 그대로).
`run.py` 진단이 쓰는 밑줄 이름 둘은 kbj 공개 별칭을 옛 이름으로 다시 붙인다(같은 함수 객체).
"""

from kbj.core.fin_rows import REPRT
from kbj.engines.financials.costs import (
    BUCKET_ORDER,
    EOK,
    REPORT_NAME_TO_Q,
    CostNote,
    annual_cost_structure,
    bucketize,
    cost_timeseries,
    fetch_cost_notes,
    find_periodic_reports,
    load_mapping,
)
from kbj.engines.financials.costs import parse_cost_table as _parse_cost_table
from kbj.engines.financials.costs import tables_with_title as _tables_with_title

from .client import DartClient

__all__ = [
    "BUCKET_ORDER",
    "EOK",
    "REPORT_NAME_TO_Q",
    "REPRT",
    "CostNote",
    "DartClient",
    "_parse_cost_table",
    "_tables_with_title",
    "annual_cost_structure",
    "bucketize",
    "cost_timeseries",
    "fetch_cost_notes",
    "find_periodic_reports",
    "load_mapping",
]
