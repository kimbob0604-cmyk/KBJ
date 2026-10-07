"""작업 처리기 계약 — 실행기가 부르는 함수의 입력(`JobContext`)·출력(`JobResult`)과 `owner` 해석.

- 처리기는 `모듈:함수` 하나다(`config/jobs.yaml` 의 `owner`).
  모양: `run(ctx: JobContext) -> JobResult`.
- 처리기는 **선점한 데이터 키(`ctx.keys`)만** 받는다. 실행기가 먼저 `ops.data_claim` 을 잡고,
  잡지 못한 키는 넘기지 않는다(설계 §6.5). 처리기가 받은 키는 `JobResult.collected` 에 돌려준다 —
  돌려주지 않은 키는 실패로 남는다(재시도가 다시 잡는다).
- `status`: `ok`(다 됐다) · `not_ready`(아직 공표 전 — 재시도 규칙대로 다시) · `failed`(오류 —
  재시도 규칙대로 다시, 다 쓰면 실패 알림) · `skipped`(할 일이 없다).
- 처리기의 예외는 실행기가 `failed` 로 바꾼다(삼키지 않는다 — 사유는 가려서 기록·알림).
- `ctx.resources` 는 서비스가 주입하는 공용 자원(Redis·DB 연결 공장·알림 함수 …)이다. 시험은 가짜를
  넣는다. 처리기는 없는 자원을 지어내지 않고 실패로 돌려준다.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final, Literal, cast

from kbj.config.settings import Settings
from kbj.data.spec import DataKey

__all__ = [
    "Handler",
    "HandlerMissing",
    "JobContext",
    "JobResult",
    "ResultStatus",
    "parse_owner",
    "resolve",
]

ResultStatus = Literal["ok", "not_ready", "failed", "skipped"]
_OWNER: Final = re.compile(
    r"(?P<mod>[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+):(?P<fn>[a-z_][a-z0-9_]*)"
)


@dataclass(frozen=True)
class JobContext:
    """처리기 입력. `now` 는 주입한 시계의 시각(UTC aware)."""

    job: str
    as_of: str
    run_id: str
    attempt: int
    now: datetime
    keys: tuple[DataKey, ...] = ()
    settings: Settings | None = None
    resources: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def resource(self, name: str) -> Any:
        """주입된 자원. 없으면 `KeyError`(처리기는 이것을 실패로 돌려준다)."""
        try:
            return self.resources[name]
        except KeyError:
            raise KeyError(f"자원 {name!r} 이 주입되지 않았다") from None


@dataclass(frozen=True)
class JobResult:
    """처리기 출력. `detail` 은 기록(`ops.job_run.detail`)에 남는 작은 dict(값·키를 싣지 않는다)."""

    status: ResultStatus
    collected: tuple[DataKey, ...] = ()
    rows: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


Handler = Callable[[JobContext], JobResult]


class HandlerMissing(LookupError):
    """`owner` 를 import 하지 못했다(또는 부를 수 없다)."""


def parse_owner(owner: str) -> tuple[str, str]:
    """`모듈:함수` → (모듈, 함수). legacy·형식 오류는 `ValueError`."""
    m = _OWNER.fullmatch(owner)
    if m is None:
        raise ValueError(f"owner 는 '모듈:함수' 여야 한다: {owner!r}")
    return m["mod"], m["fn"]


def resolve(owner: str) -> Handler:
    """`kbj.…:함수` 를 import 해 돌려준다. kbj 밖 모듈은 받지 않는다(legacy 를 부르지 않는다)."""
    mod_name, fn_name = parse_owner(owner)
    if not mod_name.startswith("kbj."):
        raise HandlerMissing(f"kbj 밖의 처리기는 부르지 않는다: {owner!r}")
    try:
        mod = importlib.import_module(mod_name)
    except ImportError as e:
        raise HandlerMissing(f"{owner}: 모듈을 import 하지 못했다({type(e).__name__})") from None
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise HandlerMissing(f"{owner}: 함수가 없다")
    return cast(Handler, fn)
