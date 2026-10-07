"""체인 행 품질 판정 (docs/phase1_design.md §5, PLAN §6.1).

레코드는 받은 순간 `ok`(또는 검증 실패 `invalid`)로 쓴다. 읽는 시점의 품질은 나이와 세션으로 정한다.

- 전광판·보강 1: 90초 안 갱신이면 ok, 넘으면 stale(직전 값 유지, PLAN §6.1). 보강 1 주기가 더
  길면(야간 B 월물 120초) 주기 + 여유
- 보강 2: 자기 순환 한 바퀴(대상 수 ÷ 속도) + 여유 안이면 ok
- estimated: 다른 원천으로 대체한 값에만 — 지금 세션에 갱신되지 않고 이전 세션(야간이면 직전 주간)
  값을 그대로 쓰는 행 (설계 §7 C: 직전 주간 스냅샷 OI)
- invalid 는 그대로 invalid
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from services.poller.records import ChainRecord, Quality, SessionName


def judge_quality(
    rec: ChainRecord, now: datetime, stale_after_s: float, session: SessionName | None
) -> Quality:
    if rec.quality == "invalid":
        return "invalid"
    if session is not None and rec.session != session:
        return "estimated"
    age = (now - rec.ts).total_seconds()
    return "ok" if age <= stale_after_s else "stale"


@dataclass(frozen=True)
class _Entry:
    rec: ChainRecord
    stale_after_s: float


BookKey = tuple[str, str, Decimal, str, str]  # (시장분류, 만기, 행사가, 콜풋, 원천)


class ChainBook:
    """(만기, 행사가, 콜풋, 원천)별 최신 행. 전광판 행과 단건 행은 따로 둔다(호가 유무가 다르다)."""

    def __init__(self) -> None:
        self._rows: dict[BookKey, _Entry] = {}

    def __len__(self) -> int:
        return len(self._rows)

    def update(self, rec: ChainRecord, stale_after_s: float) -> None:
        key = (rec.mrkt_cls, rec.expiry, rec.strike, rec.cp, rec.source)
        old = self._rows.get(key)
        if old is None or old.rec.ts <= rec.ts:
            self._rows[key] = _Entry(rec, stale_after_s)

    def view(self, now: datetime, session: SessionName | None) -> list[ChainRecord]:
        """지금 품질을 매긴 최신 행들. session 은 지금 세션(없으면 estimated 판정을 하지 않는다)."""
        out: list[ChainRecord] = []
        for e in self._rows.values():
            q = judge_quality(e.rec, now, e.stale_after_s, session)
            out.append(e.rec if q == e.rec.quality else e.rec.model_copy(update={"quality": q}))
        return out

    def get(
        self, mrkt_cls: str, expiry: str, strike: Decimal, cp: str, source: str
    ) -> ChainRecord | None:
        e = self._rows.get((mrkt_cls, expiry, strike, cp, source))
        return None if e is None else e.rec
