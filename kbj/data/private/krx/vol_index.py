"""KRX 파생상품지수 일별(`idx/drvprod_dd_trd`)에서 변동성 지수(VKOSPI) 행을 고른다(P7 — D-P7-14).

[실측 필요 #P7-1] 엔드포인트 경로·행 이름(`IDX_NM`)·종가 칸(`CLSPRC_IDX`)·단위(지수 pt)는 실제
응답으로
확인하지 않았다. 그래서 `krx.vkospi` 작업은 꺼진 채다(config/jobs.yaml). 행 이름 후보는 KRX 지수
이름
표기(공백 유무·영문) 셋 — 맞는 행이 없으면 값을 지어내지 않고 None(사유)을 돌려준다.

지수 행 모양은 다른 지수 일별과 같다고 보고 `KrxIndexDaily` 로 읽는다(같은 칸 이름 — 실측 전 가정).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from kbj.core.p7_rows import VolIndexRow
from kbj.core.quality import Quality
from kbj.data.private.krx.client import IDX_DRVPROD_DAILY
from kbj.data.private.krx.models import KrxIndexDaily, parse_rows

__all__ = ["INDEX_ID", "SOURCE", "VKOSPI_NAMES", "VolPick", "pick_vkospi"]

INDEX_ID: Final = "vkospi"
SOURCE: Final = f"KRX:{IDX_DRVPROD_DAILY.lstrip('/')}"
# 행 이름 후보 [실측 필요 #P7-1] — 공백·대소문자는 비교 전에 지운다
VKOSPI_NAMES: Final[tuple[str, ...]] = ("코스피200변동성지수", "VKOSPI", "KOSPI200변동성지수")


def _norm(s: str) -> str:
    return "".join(s.split()).upper()


@dataclass(frozen=True)
class VolPick:
    row: VolIndexRow | None
    reason: str | None
    bad_rows: int


def pick_vkospi(rows: Iterable[Mapping[str, Any]], day: date, *, received_at: datetime) -> VolPick:
    """그날 VKOSPI 한 행. 행이 없거나 이름이 맞는 행이 없으면 row None + 사유(값을 지어내지
    않는다)."""
    ok, bad = parse_rows(KrxIndexDaily, rows)
    names = {_norm(n) for n in VKOSPI_NAMES}
    hits = [r for r in ok if _norm(r.idx_nm) in names and r.bas_dd == day]
    if not ok:
        return VolPick(None, "응답 행 없음(휴장·미공표)", len(bad))
    if not hits:
        return VolPick(None, "VKOSPI 행 없음 — 행 이름 실측 필요(#P7-1)", len(bad))
    if len(hits) > 1:
        return VolPick(None, f"VKOSPI 행이 {len(hits)}개 — 행 이름 실측 필요(#P7-1)", len(bad))
    r = hits[0]
    close = r.clsprc_idx if r.clsprc_idx is not None and r.clsprc_idx > 0 else None
    row = VolIndexRow(
        trade_date=day, index_id=INDEX_ID, close=close, source=SOURCE, as_of=received_at,
        quality=Quality.OK if close is not None else Quality.INVALID,
    )  # fmt: skip
    return VolPick(row, None if row.quality is Quality.OK else "종가 없음", len(bad))
