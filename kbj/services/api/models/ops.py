"""`/api/ops/*` 응답 모델 — 페이지 13 '운영' 최소판(docs/p9_design.md §6.2, §11 Q5, ADR 0037).

보기 전용 표 셋: 서비스 상태·그날 작업·그날 발송 기록. 이름·상태·시각·개수만 — 값·키·본문이 없다.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime

from kbj.services.api.models.common import ApiModel

__all__ = [
    "OpsCheckItem",
    "OpsJobRow",
    "OpsJobs",
    "OpsNotify",
    "OpsNotifyRow",
    "OpsService",
    "OpsStatus",
]

OpsLevel = Literal["OK", "WARN", "FAIL"]


class OpsService(ApiModel):
    name: str  # 등록부 서비스 이름
    heartbeat_s: int  # 기준(초)
    age_s: int | None  # 마지막 하트비트 뒤 지난 초(None = 없음)
    level: OpsLevel


class OpsCheckItem(ApiModel):
    key: str
    title: str
    level: OpsLevel
    value: str  # 개수·시각 요약
    verdict: bool  # 1주 판정 항목인가(아니면 참고)


class OpsStatus(ApiModel):
    level: OpsLevel
    since: AwareDatetime  # 기록 구간 시작(24시간 전)
    services: list[OpsService]
    items: list[OpsCheckItem]


class OpsJobRow(ApiModel):
    job: str
    as_of: str
    attempts: int
    status: str  # 마지막 시도(ok·failed·skipped·timeout·running·queued)
    started_at: AwareDatetime | None
    finished_at: AwareDatetime | None
    retried_ok: bool
    reason: str | None = None  # 마지막 시도의 사유(가린 짧은 문장 — 실패·건너뜀·시간 초과)
    manual: bool = False  # 마지막 시도가 수동 실행(kbj.sh job)


class OpsJobs(ApiModel):
    date: date
    total: int
    failed: int
    retried_ok: int
    rows: list[OpsJobRow]


class OpsNotifyRow(ApiModel):
    kind: str
    status: str  # sent·failed·suppressed·duplicate·cooldown·queued
    requested_at: AwareDatetime
    sent_at: AwareDatetime | None
    parts: int | None
    reason: str | None  # 가린 사유(실패·억제일 때)


class OpsNotify(ApiModel):
    date: date
    sent: int
    failed: int
    rows: list[OpsNotifyRow]
