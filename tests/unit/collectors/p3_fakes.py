"""P3 수집 처리기 시험 도구 — 가짜 KIS·KRX 를 처리기 자원으로 끼운다(묶음 C).

- `DirectKis`: `KisRestClient.get` 모양. 요청을 `FakeKisServer.handle` 로 바로 넘긴다(토큰은 시험
  시작에 가짜 서버에서 한 번 받아 둔다 — auth 흉내. 리미터 없음). 부른 기록은 서버(`gets()`)와
  `calls`.
- `krx_client(fake)`: 가짜 KRX 전송을 끼운 `KrxClient`(한도 없음).
- `ctx(...)`: 처리기 입력 — 메모리 저장소·markets.yaml(파일 그대로)·KR 캘린더.
- `seed_universe`·`seed_etfs`: 합성 유니버스(가짜 KIS 의 `SYMBOLS` — 앞 10 코스피·뒤 10 코스닥)와
  ETF 일별(순자산).

키·토큰은 모두 가짜 값이다. 로그인 등급 응답은 합성.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx
import yaml
from pydantic import SecretStr

from kbj.config.markets import MarketsConfig, parse_markets
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.rows import EtfDay, UniverseRow
from kbj.data.private.kis.rest import KisResponse
from kbj.data.private.krx.client import KrxClient
from kbj.data.ratelimit import Priority
from kbj.data.spec import DataKey
from kbj.services.auth.issuer import TOKEN_PATH
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos import MemoryRepos, memory_repos
from tests.fakes.kis_server import APP_KEY, APP_SECRET, SYMBOLS, FakeKisServer
from tests.fakes.krx_server import KEY as KRX_KEY
from tests.fakes.krx_server import FakeKrx

KST: Final = ZoneInfo("Asia/Seoul")
ROOT: Final = Path(__file__).resolve().parents[3]
KR: Final = TradingCalendar.default()
ETF_CODES: Final = ("995010", "995020", "995030")


def kst(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=KST)


def load_cfg() -> MarketsConfig:
    data = yaml.safe_load((ROOT / "config" / "markets.yaml").read_text(encoding="utf-8"))
    return parse_markets(data)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


class DirectKis:
    """가짜 KIS 서버에 바로 붙는 `get`. `hook(tr_id, params, body) -> body` 로 응답을
    바꿀 수 있다."""

    def __init__(
        self,
        server: FakeKisServer,
        *,
        consumer: str = "scheduler",
        hook: Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.server = server
        self.consumer = consumer
        self.hook = hook
        self.calls: list[tuple[str, dict[str, str], Priority, float | None]] = []
        r = server.handle(
            httpx.Request(
                "POST",
                "https://fake-kis" + TOKEN_PATH,
                json={
                    "grant_type": "client_credentials",
                    "appkey": APP_KEY,
                    "appsecret": APP_SECRET,
                },
            ),
            "auth",
        )
        self.token = r.json()["access_token"]

    def get(
        self,
        path: str,
        tr_id: str,
        params: dict[str, str],
        tr_cont: str = "",
        *,
        priority: Priority = Priority.P2,
        timeout: float | None = None,
    ) -> KisResponse:
        self.calls.append((tr_id, dict(params), priority, timeout))
        req = httpx.Request(
            "GET",
            "https://fake-kis" + path,
            params=params,
            headers={
                "authorization": f"Bearer {self.token}",
                "appkey": APP_KEY,
                "appsecret": APP_SECRET,
                "tr_id": tr_id,
            },
        )
        r = self.server.handle(req, self.consumer)
        try:
            body: dict[str, Any] = r.json()
        except ValueError:
            body = {"_text": r.text[:200]}
        if self.hook is not None and r.status_code == 200:
            body = self.hook(tr_id, dict(params), body)
        return KisResponse(r.status_code, body, 0.0)

    def count(self, tr_id: str | None = None) -> int:
        return sum(1 for c in self.calls if tr_id in (None, c[0]))


def krx_client(fake: FakeKrx, key: str = KRX_KEY) -> KrxClient:
    return KrxClient(SecretStr(key), transport=fake.transport("scheduler"), now=fake._now)


def ctx(
    job: str,
    as_of: str,
    keys: Sequence[DataKey],
    *,
    now: datetime,
    repos: MemoryRepos,
    attempt: int = 1,
    **resources: Any,
) -> JobContext:
    res: dict[str, Any] = {
        "repos": repos,
        "markets": load_cfg(),
        "kr": KR,
        "kis_priority": Priority.P3,
        **resources,
    }
    return JobContext(
        job=job,
        as_of=as_of,
        run_id=f"{job}:{as_of}:{attempt}",
        attempt=attempt,
        now=now,
        keys=tuple(keys),
        resources=res,
    )


def market_of(code: str) -> str:
    return "KOSPI" if code in SYMBOLS[:10] else "KOSDAQ"


def seed_universe(repos: MemoryRepos, as_of: date, codes: Sequence[str] = SYMBOLS) -> None:
    rows = [
        UniverseRow(
            c, as_of, f"합성{c[-3:]}", market_of(c), None, date(2000, 1, 4), "krx", Quality.OK
        )
        for c in codes
    ]
    repos.market.upsert_universe(rows, loaded_by="test")


def seed_etfs(
    repos: MemoryRepos, day: date, net_assets: Sequence[int] = (900, 500, 50)
) -> list[str]:
    rows = [
        EtfDay(
            code,
            day,
            f"합성ETF{i}",
            10_000.0,
            10_000.0,
            1_000,
            n * 1_000_000_000,
            1,
            1,
            1,
            "코스피 200",
            "krx",
            "KRX",
            Quality.OK,
        )
        for i, (code, n) in enumerate(zip(ETF_CODES, net_assets, strict=True))
    ]
    repos.etf.upsert_etf_days(rows, loaded_by="test")
    return [r.code for r in rows]


def new_repos() -> MemoryRepos:
    return memory_repos()
