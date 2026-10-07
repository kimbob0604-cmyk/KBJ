"""engine 선물 지표 — Phase 3 항목 3 (docs/phase3_design.md §3·§7-3, metrics §7).

계산은 `core.metrics.futures`(순수 함수), 여기는 KIS 응답을 검증해 넘기고 행·health 를 만든다.

| 지표(`metrics.metric`) | 값 | metrics.md |
|---|---|---|
| `futures_basis` | 시장 베이시스 = 자체 선물가 − 지수(pt) | §7 |
| `futures_theory_basis` | KIS 이론가 − 지수(pt) — payload 에 KIS `basis`·차·교차검증 | §7 |
| `futures_divergence` | KIS 괴리율(%) 그대로 | §7 |
| `futures_oi_change` | KIS 미결제약정 증감(계약) 그대로 | §7 |
| `futures_strength` | KIS 체결강도(%) 그대로 | §7 |

- 입력: KIS 선물 필드가 다 있는 실측 응답은 분봉 조회(FHKIF03020200) output1 뿐이다(metrics §7
  구현 줄). 분봉은 scheduler 가 세션이 끝난 뒤(주간 16:00~·야간 06:10~) 불러 원문을 `raw_messages`
  (kis_rest)에 남긴다 — engine 은 그 원문 output1 을 1분마다 읽는다(`kis_rest_outputs`, 마지막으로
  본 시각 뒤). 행의 ts = 그 응답 수신 시각, 거래일·세션 = 원문 태그(분봉을 받은 세션), key = 선물
  종목코드, scope all, 플래그 `futures`(새 지표 기본 shadow)
- 품질: KIS 값이 있으면 ok, 없으면(빈 필드·0 이하 가격·지수) 그 값만 null·invalid(`field_missing`)
- 교차검증: |(이론가 − 지수) − KIS basis| > 0.05pt 면 health `engine_futures_basis_check`(종목마다
  10분에 한 번) — 실측으로 KIS basis 는 이론 베이시스다(metrics §7, 2026-09-30 [확인 필요])
- 기본값 [확인 필요]: 읽는 주기 60초, 기동하면 하루 앞 응답부터(같은 키라 다시 써도 행이 늘지
  않는다), 원문 태그가 없거나 검증에 실패한 응답은 건너뛰고 센다(로그)
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, cast

from core.metrics.futures import (
    BASIS_CHECK_TOLERANCE,
    FuturesMetrics,
    FuturesQuote,
    FuturesValue,
)
from data.kis.models import MinuteQuote
from data.kis.rest import MINUTE_TR
from services.engine.evaluate import EngineHealth
from services.engine.records import MetricRecord
from services.engine.registry import Flag

FUTURES_FLAG = "futures"  # 기능 플래그 — 선물 지표 다섯 개
FUTURES_TR = MINUTE_TR  # 분봉 조회(FHKIF03020200) — output1 이 선물 필드
FUTURES_EVERY_S = 60.0  # 원문을 읽는 주기 [확인 필요]
FUTURES_LOOKBACK = timedelta(days=1)  # 기동하면 이만큼 앞 응답부터 [확인 필요]
BASIS = "futures_basis"
THEORY_BASIS = "futures_theory_basis"
DIVERGENCE = "futures_divergence"
OI_CHANGE = "futures_oi_change"
STRENGTH = "futures_strength"
FUTURES_METRICS = (BASIS, THEORY_BASIS, DIVERGENCE, OI_CHANGE, STRENGTH)


def quote_of(output1: Mapping[str, Any]) -> FuturesQuote:
    """분봉 output1 → core 입력. 검증 실패면 pydantic ValidationError."""
    m = MinuteQuote.model_validate(output1)
    return FuturesQuote(
        code=m.futs_shrn_iscd,
        price=m.futs_prpr,
        basis=m.basis,
        divergence=m.dprt,
        oi_change=m.otst_stpl_qty_icdc,
        strength=m.tday_rltv,
        index=m.kospi200_nmix,
        theory_price=m.hts_thpr,
    )


def _num(v: object) -> float | None:
    return None if v is None else float(cast(Any, v))


def futures_rows(
    ts: datetime, trade_date: date, session: str, fm: FuturesMetrics, flag: Flag
) -> list[MetricRecord]:
    """§7 결과 → 지표 행 다섯(key = 선물 종목코드)."""
    common: dict[str, Any] = {
        "code": fm.code,
        "price": _num(fm.price),
        "index": _num(fm.index),
        "source": FUTURES_TR,
    }
    check: dict[str, Any] = {
        "kis_basis": fm.kis_basis.value,
        "basis_gap": _num(fm.basis_gap),
        "basis_check": fm.basis_check,
        "tolerance": float(BASIS_CHECK_TOLERANCE),
    }
    values: tuple[tuple[str, FuturesValue, dict[str, Any]], ...] = (
        (BASIS, fm.market_basis, {}),
        (THEORY_BASIS, fm.theory_basis, check),
        (DIVERGENCE, fm.divergence, {}),
        (OI_CHANGE, fm.oi_change, {}),
        (STRENGTH, fm.strength, {}),
    )
    return [
        MetricRecord(
            ts=ts,
            trade_date=trade_date,
            session=cast(Any, session),
            metric=name,
            scope="all",
            key=fm.code,
            value=v.value,
            payload={**common, **extra, "reasons": list(v.reasons)},
            quality=v.quality,
            flag=flag,
        )
        for name, v, extra in values
    ]


def basis_health(fm: FuturesMetrics) -> EngineHealth | None:
    """교차검증 실패 → health(종목이 대상). 통과·판정 없음이면 None."""
    if fm.basis_check is not False:
        return None
    return EngineHealth(
        "engine_futures_basis_check",
        "warning",
        f"{fm.code}: 이론가 − 지수 와 KIS basis {fm.kis_basis.value} 의 차 {fm.basis_gap}pt — "
        f"허용 {BASIS_CHECK_TOLERANCE}pt 초과(KIS 필드 뜻·응답 확인)",
        subject=fm.code,
    )
