"""dart-report 골든 공용 픽스처 — kbj 승격 모듈 묶음·매핑(config/dart_report/mapping.yaml)."""

from __future__ import annotations

from typing import Any

import pytest

import kbj.engines.financials.bridge as k_bridge
import kbj.engines.financials.costs as k_costs
import kbj.engines.financials.orders as k_orders
import kbj.engines.financials.statements as k_statements
import kbj.reports.dart_excel.excel as k_excel
from tests.golden.dart_report.golden import Mods


@pytest.fixture(scope="session")
def kbj_mods() -> Mods:
    return Mods(
        statements=k_statements, costs=k_costs, bridge=k_bridge, orders=k_orders, excel=k_excel
    )


@pytest.fixture(scope="session")
def mapping() -> dict[str, Any]:
    return k_costs.load_mapping()
