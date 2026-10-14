"""하루 운영 시뮬레이션 하네스 — 가짜 시계 하나로 KBJ 서비스를 함께 돌린다.

근거: 설계 §10, 메인 결정 D3. 프로세스처럼 나눠 둔다(각자 Redis 접속·KIS 클라이언트를 따로
갖는다 — 같은 fakeredis 서버):

| 이름 | 하는 일 | KIS 발급 |
|---|---|---|
| auth | `AuthService.step` 30초마다(유일한 발급자, `KBJ_SERVICE=auth`) | 한다 |
| scheduler | `SchedulerService.step`(세션 상태 + 등록부 실행기) | 안 한다 |
| notifier | `NotifierService.step`(outbox → 가짜 텔레그램, 꺼짐이면 suppressed) | — |
| gx | legacy GX external 단계 흉내(마스터·KRX 파생·분봉·poller·ws) | 안 한다 |
| legacy | legacy SD·ET 가 브리지(`kbj.data.legacy_bridge`)로 부르는 KIS 조회 | 안 한다 |

- 시계: `FakeClock` 하나(리미터·auth·스케줄러·notifier·가짜 서버가 모두 본다). 보폭은 30초
  (세션 전이 ±2분은 1초). cron 은 분 경계에 서고 실행기가 `(지난 tick, now]` 의 발화를 다
  거두므로 30초면 빠지는 발화가 없다(설계 §10.3 의 '작업 창 ±2분 1초' 대신 — 같은 결과,
  더 빠르다). 리미터 대기가 시계를 밀면 다음 경계로 이어 간다.
- 처리기: 켜진 작업 셋(`ops.nightly`·`filings.corp_code`·`ops.watchdog`)은 **실제 처리기**에
  가짜 자원을 넣어 돌린다. 아직 구현이 없는 작업은 **시뮬레이션 처리기**가 맡는다 — 선점한
  데이터 키마다 그 출처의 **실제 kbj 어댑터**(KIS·KRX·DART·ECOS·KOSIS·공공데이터포털)를 가짜
  서버에 한 번 부른다. 어댑터가 아직 없는 출처(NASDAQ·Yahoo·FRED·미 재무부·뉴욕연은·
  ForexFactory·Anthropic·ETF 운용사)는 부른 것만 기록한다(`offline_calls`).
- 알림은 아침·마감 브리핑과 하루 1회 리포트(`SIM_NOTIFY_KINDS`)만 보낸다. `alert.*` 는
  사건이 있어야 나가는데 시뮬레이션에는 사건이 없다.
- external 작업은 실행기가 돌리지 않는다(D-P2-5). gx 프로세스가 같은 데이터 키를 같은 선점
  저장소에 잡고 같은 리미터·같은 토큰 읽기로 부른다.
- DB 는 메모리(`MemoryClaimStore`·`MemoryRunLog`·`MemoryNotifyLogStore`) — 재기동해도
  남는다(DB 처럼).
- 창 시작 전 새벽에 끝났어야 할 켜진 작업(03:00 `ops.nightly`·03:05 `filings.corp_code`)은
  기록을 미리 넣는다(`seed_runs`) — 워치독이 창 밖의 일을 '놓친 실행'으로 보지 않게.

P3 확장(docs/p3_design.md §8.3 — 묶음 S): `SimOptions(p3=True)` 면 등록부에서 **켜진 P3
작업**(`krx.daily`·`market.close_collect`·`flows.intraday`·`market.intraday`·`board.daily`·
`board.confirm`·`etf.collect`·`public.export`·`market.backfill`)은 시뮬레이션 처리기 대신
**실제 처리기**가 돈다. 자원: 저장소는 `kbj.store.repos.memory`(재기동을 넘긴다 — DB 처럼),
KIS 는 scheduler 의 `KisRestClient`(같은 가짜 서버·앱키 리미터·auth 토큰 읽기), KRX 는
scheduler 의 `KrxClient`(가짜 KRX — 가짜 KIS 종목도 KRX 원장에 둔다, `kis_symbols_on_krx`),
운용사는 가짜 운용사 9곳(`tests/fakes/etf_issuer_server.py`), 공개 내보내기는 읽기 전용 확인만
통과하는 가짜 연결과 임시 산출 디렉터리. 가짜 KRX 는 시장당 몇 행뿐이라 커버리지 절대 하한은
끈다(`Coverage(min_rows={})`). 창 시작 전에 있어야 할 데이터(장중 창의 감시 ETF 일별 등)는
`SimOptions.prepare` 로 심는다.

P4 확장(docs/p4_design.md §8.5 — 묶음 S): `SimOptions(p4=True)` 면 가짜 DART·KIS 가 합성 20종목
세계(`tests/fixtures/synthetic/p4_world.py`)를 서빙하고, 등록부에서 **켜진 P4
작업**(`filings.dart_feed`·
`fin.quarterly`·`filings.derive`·`earnings.alerts`·`earnings.backfill`·`fin.valuation_band`·
`reports.dart_excel` …)과 `public.export` 는 실제 처리기가 돈다. `SimOptions.enable` 은 **이
시뮬레이션의 등록부 사본에서만** 켤 작업(운영 등록부에서 꺼진 `consensus.snapshot` — D-P4-10)이다.
창 앞 이력(공시·잠정실적·오버행·소유보고·주식총수·기업개황·관심종목·종류주식은 창 전날까지, 시세·
컨센서스는 전 거래일까지)은 저장소에 미리 넣고, 재무 5년 백필(`fin.backfill` — 상세 층 full 뒤
주요계정 main)은 창 1시간 전에 **따로 둔 가짜 DART** 로 실제 처리기를 돌린다(창의 DART 호출
수·예산에 넣지 않는다 — 운영에서는 며칠 전에 끝난 일). P3 작업은 시뮬레이션 처리기 그대로(P3 실제
처리기는 `test_one_day_p3`)라 시세는 씨앗 값을 읽는다. 공개 내보내기의 P4 표 읽기는 자원
`public_reader` (메모리 읽기 — `tests/unit/public_export/pub_rows.MemReader`)로 준다.

P5 확장(docs/p5_design.md §1.8-3·§8.4 — 웨이브 3 WRAP): `SimOptions(p5=True)` 면 합성 P5
세계(`tests/fixtures/synthetic/p5_world.py`)를 창 앞 저장소에 싣고(매크로·금투협·ETF 구성·
미국 유니버스·일봉), 등록부에서 **켜진 P5 작업**(+ `enable` 로 이 사본에서만 켠 꺼진 P5
작업 — `macro.*`·`program.*`·`market.fsc_daily`·`market_stats.kofia`)과 `public.export` 는
실제 처리기가 돈다. P5 새 출처는 가짜 서버(FRED·미 재무부·뉴욕연은·ForexFactory·Yahoo·
Nasdaq·Stooq — `tests/fakes/*_server.py`)에 물린 실제 어댑터로 부른다(리미터는 StubLimiter —
KIS 버킷은 따로 그대로 센다). F2 KIS TR 은 가짜 KIS 가 합성으로 답한다. P3·P4 작업은
시뮬레이션 처리기(P3 실제 처리기는 test_one_day_p3, P4 는 test_one_day_p4). p5 가 아닌 창의
시뮬레이션 처리기 경로에서 P5 새 출처는 여전히 부른 것만 기록한다(`OFFLINE_SOURCES`).

P7 확장(docs/p7_design.md §1.7-2 — 웨이브 3 WRAP): 등록부의 KBJ GEX 상시 서비스
(`gex.engine`·`gex.ws_gateway`·`gex.recorder` — 켜짐)는 모든 창에서 하트비트를 남긴다(이름은
`ServiceSpec.beat_name` — 프로세스 SERVICE). `SimOptions(p7=True)` 면 켜진 P7 작업(`diag.public`·
`diag.daily`·`gex.alert`)은 실제 처리기, `gex.engine` 은 실제 `EngineService`(메모리 GEX 저장소 —
`MemoryRepos.gex`)가 GX poller 흉내의 10분 회차마다 합성 체인(월물 202612 — Black-76 이론가,
`tests/unit/engines/gex/engine_inputs.synthetic_series`)으로 사이클을 돈다(주간·야간). 엔진은 KIS 를
부르지 않는다. 웹소켓·녹화 서비스는 하트비트만(구독은 위 `ws` 흉내).

키·토큰은 모두 가짜 값이다. 로그인 등급 응답은 합성(U3).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import yaml
from pydantic import SecretStr
from redis import Redis
from redis.exceptions import RedisError

from kbj.config.diag import load_diag
from kbj.config.fin import load_fin
from kbj.config.gex import load_gex
from kbj.config.macro import load_macro
from kbj.config.markets import MarketsConfig, load_markets
from kbj.config.settings import Settings
from kbj.core.calendar import (
    DAY_END,
    DAY_START,
    NIGHT_END,
    NIGHT_START,
    PRE_DAY_START,
    PRE_NIGHT_START,
    SessionInfo,
    State,
    TradingCalendar,
    night_session_opens,
    state_at,
    us_calendar,
)
from kbj.core.fin_rows import WatchItem
from kbj.core.gex.records import ExpiryRecord
from kbj.data import legacy_bridge
from kbj.data.budget import DailyBudget, krx_budget
from kbj.data.catalog import all_datasets
from kbj.data.datago import DatagoTransport
from kbj.data.limits import Limits, load_limits
from kbj.data.private.ecos_restricted import EcosRestrictedClient, require_restricted
from kbj.data.private.etf_issuers.base import IssuerHttp
from kbj.data.private.etf_issuers.registry import build as build_issuers
from kbj.data.private.forexfactory import FfClient
from kbj.data.private.fred_restricted import FredRestrictedClient
from kbj.data.private.fsc_index_price.client import FscIndexPriceClient
from kbj.data.private.fsc_stock_price.client import FscStockPriceClient
from kbj.data.private.kis.consensus import (
    TR_ESTIMATE,
    TR_OPINION,
    params_estimate,
    params_opinion,
)
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.investors_minute import TR_MARKET_MINUTE, params_market_minute
from kbj.data.private.kis.master import download_fo_master, parse_master_zip
from kbj.data.private.kis.program import INVESTOR_MARKET as PROGRAM_INVESTOR_MARKET
from kbj.data.private.kis.program import PROGRAM_TRS
from kbj.data.private.kis.rest import MINUTE_PATH, MINUTE_TR, KisRestClient, minute_chart_params
from kbj.data.private.kis.token import reader
from kbj.data.private.krx.client import KrxClient
from kbj.data.private.nasdaq.client import NasdaqClient
from kbj.data.private.stooq.client import StooqClient
from kbj.data.private.yahoo.client import YahooClient
from kbj.data.public.customs.client import DATASET_IDS as CUSTOMS_DATASETS
from kbj.data.public.dart.client import DartClient
from kbj.data.public.ecos.client import RESTRICTED_TABLES, EcosClient
from kbj.data.public.fred.client import FredClient
from kbj.data.public.fsc_kofia_stats.client import KofiaStatsClient
from kbj.data.public.kosis.client import KosisClient
from kbj.data.public.kosis.datasets import ORG_ID as KOSIS_ORG
from kbj.data.public.nyfed.client import NyfedClient
from kbj.data.public.treasury.client import TreasuryClient
from kbj.data.ratelimit import Priority, RedisRateLimiter
from kbj.data.spec import DataKey
from kbj.engines.us.config import load_us
from kbj.services.auth.service import AuthService, AuthStatus, build_auth_service, next_wait
from kbj.services.collectors import corp_code, ops_watchdog
from kbj.services.collectors import fin as fin_job
from kbj.services.collectors.corp_code import MemoryCorpCodeStore
from kbj.services.collectors.krx_daily import Coverage
from kbj.services.engine.gex import SERVICE as ENGINE_SERVICE
from kbj.services.engine.gex import EngineContext, EnginePublisher, EngineService
from kbj.services.notifier.client import NotifyClient, set_default_client
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig, load_notify_config
from kbj.services.notifier.service import NotifierService, build_service
from kbj.services.notifier.store import MemoryInboxStore, MemoryNotifyLogStore
from kbj.services.ops import nightly
from kbj.services.runtime import MemoryHealthSink
from kbj.services.runtime.bus import ChainReady
from kbj.services.runtime.heartbeat import Heartbeater, heartbeat_age
from kbj.services.scheduler.claims import (
    MemoryClaimStore,
    MemoryRunLog,
    RunRecord,
    make_run_id,
)
from kbj.services.scheduler.conditions import AsOfUnavailable, holds, resolve_as_of
from kbj.services.scheduler.handlers import Handler, JobContext, JobResult, resolve
from kbj.services.scheduler.registry import JobSpec, Registry
from kbj.services.scheduler.runner import JobRunner, inline_submit
from kbj.services.scheduler.service import SchedulerService, SessionLogRecord
from kbj.store.redis_keys import SESSION_EVENTS
from kbj.store.repos import MemoryRepos, memory_repos
from tests.acceptance.p4_pipeline import detail_codes, seed_history
from tests.fakes import (
    dart_server,
    datago_server,
    ecos_server,
    kis_server,
    kosis_server,
    krx_server,
)
from tests.fakes.clock import FakeClock
from tests.fakes.dart_server import FakeDart
from tests.fakes.datago_server import FakeDatago
from tests.fakes.ecos_server import FakeEcos
from tests.fakes.etf_issuer_server import FakeIssuers
from tests.fakes.ff_server import FakeFf
from tests.fakes.fred_server import FAKE_KEY as FRED_KEY
from tests.fakes.fred_server import FakeFred
from tests.fakes.kis_server import APP_KEY, APP_SECRET, SYMBOLS, TRS, FakeKisServer, make_client
from tests.fakes.kosis_server import FakeKosis
from tests.fakes.krx_server import FakeKrx
from tests.fakes.nasdaq_server import FakeNasdaq, FakeStooq, StubLimiter
from tests.fakes.nyfed_server import FakeNyfed
from tests.fakes.telegram_server import FakeTelegram
from tests.fakes.treasury_server import FakeTreasury
from tests.fakes.ws_server import FakeChain, FakeKisWsServer, FakeSeries, subscribe_once
from tests.fakes.yahoo_server import FakeYahoo
from tests.fixtures.synthetic.p4_world import P4World, build_world
from tests.fixtures.synthetic.p5_world import US_DAY as P5_US_DAY
from tests.fixtures.synthetic.p5_world import P5World
from tests.fixtures.synthetic.p5_world import build_world as build_p5_world
from tests.golden.dart_report.golden import NoLimit
from tests.unit.api.api_world_p4 import config_for_tests
from tests.unit.api.web_fixtures_p5.etf import load_etf as load_etf_world
from tests.unit.api.web_fixtures_p5.macro import load_macro as load_macro_world
from tests.unit.collectors.p3_fakes import seed_universe
from tests.unit.engines.filings.filings_world import load as load_filings
from tests.unit.engines.gex.engine_inputs import fut_row, seed_master, synthetic_series
from tests.unit.public_export.pub_rows import MemReader, PubWorld
from tests.unit.reports.briefs.brief_world import board_record

KST: Final = ZoneInfo("Asia/Seoul")
ROOT: Final = Path(__file__).resolve().parents[2]
STEP_S: Final = 30
FINE_S: Final = 1
FINE_AROUND_S: Final = 120  # 세션 전이 둘레 ±2분은 1초 보폭
AUTH_STEP_S: Final = 30.0

# 가짜 봇 — 공개 안전 검사(텔레그램 토큰 형태)에 걸리지 않게 조각으로
TG_TOKEN: Final = "123456789:" + "AAFakeSimulationToken" + "_abcdefghijk0123"
TG_CHAT: Final = "-1009876543210"

# 알림을 보내는 시뮬레이션 처리기 종류 — 하루 1회 리포트만(alert.* 는 사건이 있어야 나간다)
SIM_NOTIFY_KINDS: Final = frozenset(
    {"brief.morning", "brief.closing", "flows.report", "etf.report"}
)
REAL_HANDLERS: Final[dict[str, Handler]] = {
    "kbj.services.ops.nightly:run": nightly.run,
    "kbj.services.collectors.corp_code:run": corp_code.run,
    "kbj.services.collectors.ops_watchdog:run": ops_watchdog.run,
}
# 시뮬레이션 처리기 경로에서 부른 것만 기록하는 출처. P5 새 출처(NASDAQ·YAHOO·FRED·
# TREASURY·NYFED·FF)는 p5 창에서 실제 처리기 + 가짜 서버로 부른다(위 머리말). ANTHROPIC(구루 X
# 다이제스트)은 사용자 결정(2026-10-13 Q4)으로 뺐다. ETF_ISSUERS 는 P3 실제 처리기가 가짜
# 운용사로 부른다(p3 창)
OFFLINE_SOURCES: Final = frozenset(
    {"NASDAQ", "YAHOO", "FRED", "TREASURY", "NYFED", "FF", "ETF_ISSUERS"}
)

# KIS 거래소 구분 파라미터 [실측 필요 — kbj/data/private/kis/datasets.py 머리말: J·NX·UN 추정]
VENUE_CODE: Final = {"KRX": "J", "NXT": "NX", "TOTAL": "UN", "": "J"}
MARKETS: Final = ("0001", "1001")  # 코스피·코스닥 업종 코드 [추정]
WATCH: Final = SYMBOLS[:5]  # 알림 규칙 관심종목(합성)
ETF_CODE: Final = "990990"  # 합성 ETF
FUT_CODE: Final = (
    "A01612"  # 합성 마스터의 선물 코드(tests/fixtures/synthetic/kis/master_lines.json)
)


# P7 GEX 엔진 사이클의 합성 체인 — 월물 202612(최종거래일 12-10 둘째 목요일), 행사가 범위는
# tests/fakes/ws_server.default_chain 의 월물과 같다. 선물 근월물은 그 마스터의 A01612(F 202612)
P7_MONTH: Final = "202612"
P7_EXPIRY: Final = date(2026, 12, 10)
P7_CHAIN: Final = FakeChain(
    series=(FakeSeries("", P7_MONTH, P7_EXPIRY, Decimal("745.0"), Decimal("1595.0")),)
)


def kst(d: date, t: dtime) -> datetime:
    return datetime.combine(d, t, tzinfo=KST)


@dataclass(frozen=True)
class KisSpec:
    """KIS 논리 데이터셋 → TR 한 번(들). per: 종목마다(symbols)·시장마다(markets)·한 번(None)."""

    tr_id: str
    per: tuple[str, ...] | None
    params: Callable[[str, str, str], dict[str, str]]  # (venue 코드, 대상, YYYYMMDD) → 파라미터
    priority: Priority = Priority.P3


def _stock(v: str, code: str, _d: str) -> dict[str, str]:
    return {"FID_COND_MRKT_DIV_CODE": v, "FID_INPUT_ISCD": code}


def _opinion_params(code: str, ymd: str) -> dict[str, str]:
    """종목투자의견 기간 = 그날 − config/fin.yaml consensus.opinion_window_days ~ 그날(묶음 K)."""
    end = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:8]))
    window = load_fin().consensus.opinion_window_days
    return params_opinion(code, end - timedelta(days=window), end)


KIS_SPECS: Final[dict[str, KisSpec]] = {
    "stock_quote_eod": KisSpec("FHKST01010100", SYMBOLS, _stock),
    "stock_investor_daily": KisSpec("FHKST01010900", SYMBOLS, _stock),
    "market_investor_daily": KisSpec(
        "FHPTJ04040000",
        MARKETS,
        lambda v, m, d: {"FID_COND_MRKT_DIV_CODE": v, "FID_INPUT_ISCD": m, "FID_INPUT_DATE_1": d},
    ),
    "inst_foreign_top": KisSpec(
        "FHPTJ04400000",
        None,
        lambda v, _c, _d: {"FID_COND_MRKT_DIV_CODE": v, "FID_INPUT_ISCD": "0000"},
    ),
    "inst_foreign_intraday": KisSpec(
        "FHPTJ04400000",
        None,
        lambda v, _c, _d: {"FID_COND_MRKT_DIV_CODE": v, "FID_INPUT_ISCD": "0000"},
    ),
    "turnover_rank_intraday": KisSpec(
        "FHPST01710000",
        None,
        lambda v, _c, _d: {"FID_COND_MRKT_DIV_CODE": v, "FID_BLNG_CLS_CODE": "3"},
    ),
    "etf_quote_intraday": KisSpec("FHPST02400000", (ETF_CODE,), _stock),
    # P3(묶음 M 웨이브 1 — docs/p3_design.md §3.3). 실제 처리기는 묶음 C, 시뮬레이션 확장은
    # 묶음 S(§8.3)
    "etf_investor_daily": KisSpec("FHKST01010900", (ETF_CODE,), _stock),
    "index_quote_intraday": KisSpec(  # [추정 TR] 코스피·코스닥·코스피200 — 슬롯당 3건
        "FHPUP02100000",
        (*MARKETS, "2001"),
        lambda _v, c, _d: {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": c},
    ),
    "sector_quote_intraday": KisSpec(  # [추정 TR] 시장마다 1건 — 슬롯당 2건
        "FHPUP02140000",
        MARKETS,
        lambda _v, c, _d: {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": c},
    ),
    "watch_quotes_intraday": KisSpec("FHKST01010100", WATCH, _stock, Priority.P2),
    # P4 컨센서스(묶음 K) — TR 은 kis/datasets.py:CONSENSUS_TRS 상수([확인 필요 #29]), 파라미터는
    # 파서 모듈의 params_*(같은 모양). consensus.snapshot 은 운영 등록부에서 꺼져 있어도 이 하루
    # 시뮬레이션은 가짜 처리기로 돌린다(docs/p4_design.md §0.4 끝)
    "consensus_estimate": KisSpec(TR_ESTIMATE.tr_id, SYMBOLS, lambda _v, c, _d: params_estimate(c)),
    "invest_opinion": KisSpec(TR_OPINION.tr_id, SYMBOLS, lambda _v, c, d: _opinion_params(c, d)),
    # P5 F2(묶음 F2 — TR·입력 칸은 파서 모듈 kbj.data.private.kis.{investors_minute,program}, 전부
    # [확인 필요 #39~#41]). 1분 투자자 조합은 설정의 켠 것(실측 전 K2I 셋 — 현물 코드는 #39 뒤)
    "market_investor_intraday": KisSpec(
        TR_MARKET_MINUTE.tr_id,
        tuple(f"{m}:{s}" for m, s in load_markets().flows.minute.enabled_pairs()),
        lambda _v, t, _d: params_market_minute(*t.split(":")),
        Priority.P2,
    ),
    "program_market_intraday": KisSpec(
        PROGRAM_TRS["program_market_intraday"].tr_id,
        ("K", "Q"),
        lambda v, m, _d: {
            "FID_COND_MRKT_DIV_CODE": v,
            "FID_MRKT_CLS_CODE": m,
            "FID_SCTN_CLS_CODE": "",
            "FID_INPUT_ISCD": "",
            "FID_COND_MRKT_DIV_CODE1": "",
            "FID_INPUT_HOUR_1": "",
        },
        Priority.P2,
    ),
    "program_market_daily": KisSpec(
        PROGRAM_TRS["program_market_daily"].tr_id,
        ("K", "Q"),
        lambda v, m, d: {
            "FID_COND_MRKT_DIV_CODE": v,
            "FID_MRKT_CLS_CODE": m,
            "FID_INPUT_DATE_1": d,
            "FID_INPUT_DATE_2": d,
        },
    ),
    "program_investor_today": KisSpec(
        PROGRAM_TRS["program_investor_today"].tr_id,
        ("K", "Q"),
        lambda v, m, _d: {"EXCH_DIV_CLS_CODE": v, "MRKT_DIV_CLS_CODE": PROGRAM_INVESTOR_MARKET[m]},
    ),
    "program_stock_intraday": KisSpec(PROGRAM_TRS["program_stock_intraday"].tr_id, SYMBOLS, _stock),
    "program_stock_daily": KisSpec(  # event 키 — 실행기가 미리 잡지 않는다(처리기가 직접 선점)
        PROGRAM_TRS["program_stock_daily"].tr_id,
        SYMBOLS,
        lambda v, c, d: {"FID_COND_MRKT_DIV_CODE": v, "FID_INPUT_ISCD": c, "FID_INPUT_DATE_1": d},
        Priority.P4,
    ),
}


class SimUnsupported(RuntimeError):
    """시뮬레이션 처리기가 모르는 데이터셋 — 시험이 잡는다(조용히 성공으로 두지 않는다)."""


@dataclass
class SessionLogMemory:
    """`ops.session_log` 메모리판(SchedulerStore)."""

    rows: list[SessionLogRecord] = field(default_factory=list[SessionLogRecord])

    def write_session_log(self, rows: Any) -> None:
        self.rows.extend(rows)

    def flush_spool(self) -> bool:
        return True


class _FakeCursor:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.rowcount = 0

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        self._log.append(" ".join(str(query).split()[:4]))


class _FakeConn:
    """`ops.nightly` 정리 단계용 가짜 접속(지울 행 0)."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def __enter__(self) -> _FakeConn:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._log)


def kis_symbols_on_krx(day: date = date(2026, 1, 2)) -> dict[str, list[dict[str, Any]]]:
    """가짜 KIS 종목(`SYMBOLS` — 앞 10 코스피·뒤 10 코스닥, `market_symbols`)을 가짜 KRX
    일별·기본정보에도
    둔다(P3 — 실제 KRX 는 KIS 가 주는 종목을 모두 덮는다). 이미 KRX 템플릿에 있는 코드는 건너뛴다.
    값은 KRX 템플릿 첫 행을 틀로(코드·이름·ISIN 만 바꾼다) — 합성."""
    out: dict[str, list[dict[str, Any]]] = {}
    for market, stock_ep, base_ep in (
        ("0001", "/sto/stk_bydd_trd", "/sto/stk_isu_base_info"),
        ("1001", "/sto/ksq_bydd_trd", "/sto/ksq_isu_base_info"),
    ):
        tmpl_stock = krx_server.rows_for(stock_ep, day)
        tmpl_base = krx_server.rows_for(base_ep, day)
        have = {str(r["ISU_CD"]) for r in tmpl_stock}
        for code in kis_server.market_symbols(market):
            if code in have:
                continue
            name = f"합성{code[-3:]}"
            out.setdefault(stock_ep, []).append({**tmpl_stock[0], "ISU_CD": code, "ISU_NM": name})
            out.setdefault(base_ep, []).append(
                {**tmpl_base[0], "ISU_CD": f"KR7{code}001", "ISU_SRT_CD": code, "ISU_NM": name,
                 "ISU_ABBRV": name}
            )  # fmt: skip
    return out


class _PublicCursor:
    """`public.export` 연결 확인(읽기 전용·구성원) SQL 에 ('on', True) 로 답하는 가짜 커서."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def __enter__(self) -> _PublicCursor:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        self._log.append(" ".join(str(query).split()[:4]))

    def fetchone(self) -> tuple[object, object]:
        return ("on", True)


class _PublicConn:
    """공개 내보내기 역할(`kbj_public_export`) 연결 흉내 — P3 는 표를 읽지 않고 확인만
    한다(§7.1)."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    def __enter__(self) -> _PublicConn:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def cursor(self) -> _PublicCursor:
        return _PublicCursor(self._log)


@dataclass
class SimOptions:
    start: datetime
    hours: float = 24.0
    notify_enabled: bool = False
    krx_publish_time: dtime = dtime(8, 0)  # KRX D+1 공표(conflict_map E1)
    seed_runs: bool = True
    ws: bool = True
    legacy_calls: bool = True
    redis_down: tuple[datetime, datetime] | None = None
    restarts: tuple[datetime, ...] = ()
    hooks: tuple[tuple[datetime, Callable[[SimDay], None]], ...] = ()
    p3: bool = False  # 켜진 P3 작업은 실제 처리기로(위 머리말)
    prepare: tuple[Callable[[SimDay], None], ...] = ()  # 만든 뒤 run 전에 한 번(데이터 심기)
    p4: bool = False  # 합성 20종목 세계 + 켜진 P4 작업은 실제 처리기로(위 머리말)
    enable: tuple[str, ...] = ()  # 이 시뮬레이션의 등록부 사본에서만 켤 작업(운영 등록부는 그대로)
    p5: bool = False  # 합성 P5 세계 + 켜진 P5 작업은 실제 처리기로(위 머리말)
    p7: bool = False  # 켜진 P7 작업은 실제 처리기 + 실제 GEX 엔진 사이클(위 머리말)
    drop_minutes: frozenset[str] = (
        frozenset()
    )  # P5 F2 실패 변형 — 이 분(HH:MM)의 1분 수집은 가짜 KIS 오류


def load_registry(enable: tuple[str, ...] = ()) -> Registry:
    """운영 등록부(`config/jobs.yaml`) — `enable` 의 작업만 켠 사본(없는 이름이면 실패)."""
    path = ROOT / "config" / "jobs.yaml"
    if not enable:
        return Registry.load(path)
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    names = set(enable)
    for j in data["jobs"]:
        if j["name"] in names:
            j["enabled"] = True
            names.discard(j["name"])
    if names:
        raise ValueError(f"등록부에 없는 작업: {sorted(names)}")
    return Registry.parse(data)


@dataclass
class ExternalRun:
    job: str
    as_of: str
    at: datetime
    status: str  # ok · duplicate · not_ready · failed
    detail: str = ""


class SimDay:
    """하루(창) 시뮬레이션. `run()` 뒤 속성으로 결과를 본다. 끝나면 `close()`."""

    def __init__(self, opts: SimOptions) -> None:
        if opts.start.tzinfo is None:
            raise ValueError("naive datetime 금지")
        self.opts = opts
        self.start = opts.start
        self.end = opts.start + timedelta(hours=opts.hours)
        self.clock = FakeClock(opts.start)
        self.redis_server = fakeredis.FakeServer()
        self.kr = TradingCalendar.default()
        self.us = us_calendar()
        # ── 가짜 서버 ─────────────────────────────────────────────────────────────────
        now = self.clock.now
        self.world: P4World | None = build_world() if opts.p4 else None
        self.kis = FakeKisServer(now, consensus=self.world)
        self.krx = FakeKrx(now, extra=kis_symbols_on_krx() if opts.p3 else None)
        self.dart = FakeDart(now, world=self.world)
        self.datago = FakeDatago(now)
        self.ecos = FakeEcos(now)
        self.kosis = FakeKosis(now)
        self.tg = FakeTelegram(now)
        self.ws: FakeKisWsServer | None = None
        if opts.ws:
            self.ws = FakeKisWsServer(self.kis.valid_approval_key).start()
        self._publish_krx()
        # ── 남는 것(DB 처럼 재기동을 넘긴다) ──────────────────────────────────────────────
        self.registry = load_registry(opts.enable)
        self.catalog = all_datasets()
        self.claims = MemoryClaimStore()
        self.runs = MemoryRunLog()
        self.notify_log = MemoryNotifyLogStore()
        self.inbox = MemoryInboxStore()
        self.corp_store = MemoryCorpCodeStore()
        self.session_log = SessionLogMemory()
        self.pg_log: list[str] = []
        self.backups: list[str] = []
        self.offline_calls: list[tuple[str, DataKey, datetime]] = []
        self.unsupported: list[str] = []
        self.kis_requests: list[tuple[str, DataKey, str]] = []  # (작업, 키, 대상) — 성공 조회
        self.external_runs: list[ExternalRun] = []
        self.ws_results: list[tuple[datetime, dict[str, Any], dict[str, Any]]] = []
        self.legacy_results: list[tuple[datetime, int, str | None]] = []
        self.session_events: list[dict[str, Any]] = []
        self.auth_statuses: list[AuthStatus] = []
        self.restarted_at: list[datetime] = []
        self.ticks = 0
        # P3: 저장소(DB 처럼 재기동을 넘긴다)·가짜 운용사·공개 산출 디렉터리
        self.repos: MemoryRepos = memory_repos()
        self.issuers = FakeIssuers(latest=self.start.astimezone(KST).date())
        self.public_log: list[str] = []
        self.public_out = Path(tempfile.mkdtemp(prefix="kbj-sim-public-"))
        self.p3_owners: frozenset[str] = frozenset(
            j.owner for j in self.registry.jobs if opts.p3 and j.phase == "P3" and j.enabled
        )
        # P4: 켜진 P4 작업(+ enable) + 공개 내보내기(P4 파일을 쓴다)는 실제 처리기
        self.p4_owners: frozenset[str] = frozenset(
            j.owner
            for j in self.registry.jobs
            if opts.p4 and j.enabled and (j.phase == "P4" or j.name == "public.export")
        )
        self.p4_seed_runs: list[JobResult] = []
        if opts.p4:
            self._p4_seed()
        # P5: 켜진 P5 작업(+ enable) + 공개 내보내기(macro/·credit.json 을 쓴다)는 실제 처리기
        self.p5w: P5World | None = build_p5_world() if opts.p5 else None
        self.p5_owners: frozenset[str] = frozenset(
            j.owner
            for j in self.registry.jobs
            if opts.p5 and j.enabled and (j.phase == "P5" or j.name == "public.export")
        )
        self._p5_fakes()
        if opts.p5:
            self._p5_seed()
        # P7: 켜진 P7 작업은 실제 처리기(diag.public·diag.daily·gex.alert)
        self.p7_owners: frozenset[str] = frozenset(
            j.owner for j in self.registry.jobs if opts.p7 and j.enabled and j.phase == "P7"
        )
        if opts.seed_runs:
            self._seed_runs()
        # ── 프로세스 ─────────────────────────────────────────────────────────────────
        self.auth_health = MemoryHealthSink()
        self.sched_health = MemoryHealthSink()
        self.notifier_health = MemoryHealthSink()
        self._auth_setup()
        self._notifier_setup()
        self._scheduler_setup()
        self._gx_setup()
        self._legacy_setup()
        self._observer = self._redis().pubsub(ignore_subscribe_messages=True)
        self._observer.subscribe(SESSION_EVENTS)
        self._hooks = sorted(opts.hooks, key=lambda h: h[0])
        self._restarts = sorted(opts.restarts)
        self._closed = False
        for fn in opts.prepare:
            fn(self)

    # ── 조립 ──────────────────────────────────────────────────────────────────────────
    def _redis(self) -> Redis:
        return fakeredis.FakeRedis(server=self.redis_server)

    def settings(self, service: str) -> Settings:
        extra: dict[str, Any] = {}
        if self.opts.notify_enabled:
            extra = {
                "telegram_bot_token": SecretStr(TG_TOKEN),
                "telegram_chat_id": SecretStr(TG_CHAT),
            }
        return Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            service=service,
            kis_app_key=SecretStr(APP_KEY),
            kis_app_secret=SecretStr(APP_SECRET),
            krx_api_key=SecretStr(krx_server.KEY),
            dart_api_key=SecretStr(dart_server.KEY),
            datago_key=SecretStr(datago_server.KEY),
            ecos_key=SecretStr(ecos_server.KEY),
            kosis_key=SecretStr(kosis_server.KEY),
            redis_url=SecretStr("redis://sim.invalid:6379/0"),
            notify_enabled=self.opts.notify_enabled,
            config_dir=ROOT / "config",
            **extra,
        )

    def _limits(self, s: Settings) -> Limits:
        return load_limits(settings=s)

    def _publish_krx(self) -> None:
        d = self.start.astimezone(KST).date()
        while d <= self.end.astimezone(KST).date():
            if self.kr.is_trading_day(d):
                prev = self.kr.prev_trading_day(d)
                self.krx.publish_at(prev, kst(d, self.opts.krx_publish_time))
            d += timedelta(days=1)

    def _seed_runs(self) -> None:
        """창 시작 전 그날 새벽에 끝났어야 할 켜진 cron 작업 기록(DB 에 이미 있는 것처럼)."""
        before = self.start - timedelta(seconds=1)
        for job in self.registry.enabled_jobs():
            spec = job.schedule.cron_spec()
            if spec is None:
                continue
            due = spec.prev_at_or_before(before.astimezone(job.schedule.zone))
            if due is None or due.date() != self.start.astimezone(job.schedule.zone).date():
                continue
            if not holds(
                job.schedule.when, due.date(), self.kr, self.us, month_days=job.schedule.month_days
            ):
                continue
            try:
                as_of = resolve_as_of(job.run_as_of(), due, self.kr, self.us)
            except AsOfUnavailable:
                continue
            self.runs.record(
                RunRecord(
                    make_run_id(job.name, as_of, 1),
                    job.name,
                    as_of,
                    1,
                    "ok",
                    due,
                    due,
                    {"seeded": True},
                )
            )

    # auth ─────────────────────────────────────────────────────────────────────────────
    def _auth_setup(self) -> None:
        s = self.settings("auth")
        self.auth_redis = self._redis()
        creds = KisCredentials.from_settings(s)
        self.auth_http = httpx.Client(base_url=creds.base_url, transport=self.kis.transport("auth"))
        cfg = self._limits(s).source("kis").rate_config()
        limiter = RedisRateLimiter.scoped(self.auth_redis, "kis", APP_KEY, cfg, self.clock)
        self.auth: AuthService = build_auth_service(
            s,
            self.auth_redis,
            self.auth_http,
            self.auth_health,
            limiter=limiter,
            now=self.clock.now,
        )
        self.auth_hb = Heartbeater(self.auth_redis, "auth", ttl_s=180, now=self.clock.now)
        self._auth_next: datetime | None = None

    def _auth_tick(self, now: datetime) -> None:
        if self._auth_next is not None and now < self._auth_next:
            return
        st = self.auth.step(now)
        self.auth_statuses.append(st)
        self.auth_hb.beat(ok=st.ok)
        self._auth_next = now + timedelta(seconds=next_wait(st, AUTH_STEP_S))

    # notifier ─────────────────────────────────────────────────────────────────────────
    def _notifier_setup(self) -> None:
        s = self.settings("notifier")
        self.notifier_redis = self._redis()
        self.notify_config: NotifyConfig = load_notify_config(settings=s)
        self.notifier: NotifierService = build_service(
            s,
            config=self.notify_config,
            redis=self.notifier_redis,
            log_store=self.notify_log,
            inbox_store=self.inbox,
            now=self.clock.now,
            health=self.notifier_health,
            telegram_transport=self.tg.transport,
            limiter_clock=self.clock,
            sleep=self.clock.sleep,
        )
        self.notifier_hb = Heartbeater(self.notifier_redis, "notifier", now=self.clock.now)

    # scheduler ────────────────────────────────────────────────────────────────────────
    def _scheduler_setup(self) -> None:
        s = self.settings("scheduler")
        self.sched_settings = s
        r = self._redis()
        self.sched_redis = r
        limits = self._limits(s)
        clock = self.clock
        self.notify_client = NotifyClient(
            self.notify_config, s.notify_enabled, outbox=Outbox(r, now=clock.now), now=clock.now
        )
        set_default_client(self.notify_client)  # 작업이 부르는 shim 도 같은 대기열로
        creds = KisCredentials.from_settings(s)
        self.sched_kis: KisRestClient = make_client(
            self.kis, creds, r, consumer="scheduler", clock=clock, now=clock.now
        )
        self.sched_krx = KrxClient.from_settings(
            s,
            r,
            limits=limits,
            rate_clock=clock,
            transport=self.krx.transport("scheduler"),
            clock=clock.monotonic,
            now=clock.now,
        )
        self.sched_dart = DartClient.from_settings(
            s,
            r,
            limits=limits,
            clock=clock,
            transport=self.dart.transport("scheduler"),
            now=clock.now,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        assert s.datago_key is not None
        self.sched_datago = DatagoTransport.for_datasets(
            s.datago_key,
            r,
            ["15094808", "15094807", "15094809"],
            limits=limits,
            clock=clock,
            transport=self.datago.transport("scheduler"),
            now=clock.now,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
        ecos_kw: dict[str, Any] = {"now": clock.now, "monotonic": clock.monotonic}
        self.sched_ecos = EcosClient.from_settings(
            s, r, limits=limits, clock=clock, transport=self.ecos.transport("scheduler"), **ecos_kw
        )
        self.sched_ecos_restricted = EcosRestrictedClient.from_settings(
            s, r, limits=limits, clock=clock, transport=self.ecos.transport("scheduler"), **ecos_kw
        )
        self.sched_kosis = KosisClient.from_settings(
            s,
            r,
            limits=limits,
            clock=clock,
            transport=self.kosis.transport("scheduler"),
            monotonic=clock.monotonic,
        )
        self.sched_hb = Heartbeater(r, "scheduler", now=clock.now)
        self.runner = self._make_runner()
        self.scheduler = SchedulerService(
            r, self.runner, self.kr, self.session_log, now=clock.now, health=self.sched_health
        )

    def _make_runner(self) -> JobRunner:
        handlers: dict[str, Handler] = {}
        for job in self.registry.jobs:
            if job.external:
                continue
            if job.owner in handlers:
                raise AssertionError(
                    f"owner 가 겹친다 — 시뮬레이션 처리기를 나눌 수 없다: {job.owner}"
                )
            if (
                job.owner in self.p3_owners
                or job.owner in self.p4_owners
                or (job.owner in self.p5_owners)
                or (job.owner in self.p7_owners)
            ):
                handlers[job.owner] = resolve(job.owner)  # 실제 P3·P4·P5 처리기(import 검증 겸)
                continue
            handlers[job.owner] = REAL_HANDLERS.get(job.owner) or self._sim_handler(job)
        resources: dict[str, Any] = {
            "registry": self.registry,
            "runs": self.runs,
            "kr": self.kr,
            "us": self.us,
            "notify": self.notify_client.notify,
            "notify_media": self.notify_client.notify_media,  # P5 수급 리포트 차트(BRF)
            "running": lambda: [r for r in self.runs.records.values() if r.status == "running"],
            "heartbeat_age": lambda svc, now: heartbeat_age(self.sched_redis, svc, now),
            "corp_code_store": self.corp_store,
            "corp_code_fetch": lambda: self.sched_dart.corp_codes(refresh=True),
            "backup": self._backup,
            "inbox_days": self.notify_config.inbox.keep_days,
            "connect": lambda: _FakeConn(self.pg_log),
            "redis": self.sched_redis,
            # P4(D-P4-25 ②) — event 키는 처리기가 실행기와 같은 선점 저장소로 직접 잡는다(운영
            # scheduler/__main__.py 와 같은 자원)
            "claims": self.claims,
        }
        if self.opts.p3:
            resources.update(self._p3_resources())
        if self.opts.p4:
            resources.update(self._p4_resources())
        if self.opts.p5:
            resources.update(self._p5_resources())
        if self.opts.p7:
            resources.update(self._p7_resources())
        return JobRunner(
            self.registry,
            handlers,
            self.claims,
            self.kr,
            self.us,
            runs=self.runs,
            catalog=self.catalog,
            health=self.sched_health,
            submit=inline_submit,
            notify=self.notify_client.notify,
            settings=self.sched_settings,
            resources=resources,
        )

    def markets(self) -> MarketsConfig:
        return load_markets(settings=self.sched_settings)

    def _p3_resources(self) -> dict[str, Any]:
        """실제 P3 처리기의 자원(kbj/services/collectors/_p3.py 머리말·각 처리기 머리말)."""
        http = IssuerHttp(httpx.Client(transport=self.issuers.transport()), sleep=lambda _s: None)
        return {
            "repos": self.repos,
            "kis": self.sched_kis,
            "krx": self.sched_krx,
            "krx_backfill": self.sched_krx,
            "markets": self.markets(),
            "krx_coverage": Coverage(min_rows={}),
            "etf_issuers": build_issuers(
                http, None, today=lambda: self.clock.now().astimezone(KST).date()
            ),
            "parallel": False,
            "public_connect": lambda: _PublicConn(self.public_log),
            "public_out_dir": self.public_out,
        }

    # ── P4(docs/p4_design.md §8.5) ─────────────────────────────────────────────────────
    def _p4_seed(self) -> None:
        """창 앞 이력을 저장소에 넣고 재무 5년 백필을 실제 처리기로 돌린다(위 머리말)."""
        w = self.world
        assert w is not None
        day = self.start.astimezone(KST).date()
        eve = kst(day - timedelta(days=1), dtime(23, 59))  # 창 전날 끝 — 그날 공시는 피드가 받는다
        self.repos = load_filings(eve, w=w).repos
        seed_history(self.repos, w, until=self.kr.prev_trading_day(day), now=eve)
        at = self.start - timedelta(hours=1)
        clock = FakeClock(at)
        fake = FakeDart(clock.now, world=w)
        client = DartClient(
            dart_server.KEY, transport=fake.transport("seed"), limiter=NoLimit(), retries=1,
            backoff_s=0, now=clock.now, sleep=lambda _s: None,
        )  # fmt: skip
        fin_cfg, reports_cfg = config_for_tests()
        for tier in ("full", "main"):
            cfg = fin_cfg.model_copy(
                update={"backfill": fin_cfg.backfill.model_copy(update={"tier": tier})}
            )
            ctx = JobContext(
                job="fin.backfill", as_of=at.astimezone(KST).strftime("%Y-%m-%dT%H:%M"),
                run_id=f"fin.backfill:seed:{tier}", attempt=1, now=at, keys=(),
                resources={
                    "public_repos": self.repos.public(), "dart": client, "claims": self.claims,
                    "fin_config": cfg, "reports_config": reports_cfg,
                    "detail_universe": lambda _d: detail_codes(self.repos),
                },
            )  # fmt: skip
            res = fin_job.backfill(ctx)
            if res.status != "ok":
                raise RuntimeError(f"재무 백필 씨앗 실패: {res.status} {dict(res.detail)}")
            self.p4_seed_runs.append(res)
        # 씨앗 공시(창 앞 정기보고서)는 백필이 받은 것 — 운영에서는 이 공시들이 피드에 들어올 때
        # 이미 fin.quarterly 가 처리 표시를 찍었다. 창의 fin.quarterly 는 그날 접수된 보고서만
        # 받는다
        old = self.repos.filings.fin_unloaded(kinds=("periodic",), limit=1_000_000)
        self.repos.filings.mark_fin_loaded([d.rcept_no for d in old], now=at)

    def _p4_resources(self) -> dict[str, Any]:
        """실제 P4 처리기의 자원(각 처리기 머리말 — 운영은 연결로 만든다)."""
        w = self.world
        assert w is not None
        fin_cfg, reports_cfg = config_for_tests()
        corp_rows: list[dict[str, Any]] = [
            {
                "corp_code": c.corp_code,
                "stock_code": c.stock_code,
                "corp_name": c.corp_name,
                "modify_date": date(2026, 10, 1),
                "source": "DART",
                "quality": "ok",
                "received_at": self.start,
            }
            for c in w.corps
        ]
        pub = self.repos.public()
        return {
            "repos": self.repos,
            "public_repos": pub,
            "journal": self.repos.journal,
            "dart": self.sched_dart,
            "kis": self.sched_kis,
            "fin_config": fin_cfg,
            "reports_config": reports_cfg,
            "detail_universe": lambda _d: detail_codes(self.repos),
            "public_connect": lambda: _PublicConn(self.public_log),
            "public_out_dir": self.public_out,
            "public_reader": MemReader(PubWorld(repos=pub, corp_code_rows=corp_rows)),
        }

    # ── P5(docs/p5_design.md §8.4) ─────────────────────────────────────────────────────
    def _p5_fakes(self) -> None:
        """P5 새 출처의 가짜 서버(같은 가짜 시계). Nasdaq 은 합성 P5 세계의 미국 일봉을 서빙한다."""
        now = self.clock.now
        self.fred = FakeFred(now)
        self.treasury = FakeTreasury(now)
        self.nyfed = FakeNyfed(now)
        self.ff = FakeFf(now)
        self.yahoo = FakeYahoo(now)
        self.nasdaq = FakeNasdaq()
        self.stooq = FakeStooq()
        w = self.p5w
        if w is None:
            return
        for u in w.us_universe:
            bars = [
                {"asof": b.date.isoformat(), "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": float(b.volume or 0)}
                for b in w.us_bars(u.symbol)
            ]  # fmt: skip
            self.nasdaq.add_series(u.symbol, bars=bars)

    def _p5_seed(self) -> None:
        """창 앞 이력(매크로·금투협·캘린더, ETF 구성·변동, 미국 유니버스·일봉)을 저장소에 넣는다."""
        w = self.p5w
        assert w is not None
        load_macro_world(self.repos, w)
        load_etf_world(self.repos, w)
        eve = self.start - timedelta(hours=1)
        self.repos.us.put_universe(
            P5_US_DAY, list(w.us_universe), loaded_by="sim.seed", received_at=eve
        )
        day = self.start.astimezone(KST).date()
        prior = [
            b
            for u in w.us_universe
            for b in w.us_bars(u.symbol)
            if b.date < day - timedelta(days=1)
        ]
        self.repos.us.upsert_daily(prior, loaded_by="sim.seed", received_at=eve)
        # KRX 일봉(합성 24종목) — 수급 리포트 차트·수익률 입력
        self.repos.market.upsert_daily_bars(
            [b for st in w.stocks for b in w.bars(st.code)], loaded_by="sim.seed", received_at=eve
        )
        # 프로그램 훑기·마감 훑기 대상(P3 krx.daily·flows.intraday 는 시뮬레이션 처리기라
        # 저장소에 쓰지 않는다): 가짜 KIS 종목 유니버스 + 관심종목 둘
        seed_universe(self.repos, day, SYMBOLS)
        self.repos.journal.replace_watchlist(
            "main", [WatchItem("main", c, None, None, i) for i, c in enumerate(SYMBOLS[:2])],
            loaded_by="sim.seed", now=eve,
        )  # fmt: skip
        # 그날 보드 산출(board.daily 는 시뮬레이션 처리기 — 16:03 산출을 미리 둔다: 마감 요약 ③·
        # 수급 리포트 대상). 합성 — tests/unit/reports/briefs/brief_world.board_record
        self.repos.board.put_day(board_record(day), loaded_by="sim.seed", now=eve)

    def _p7_resources(self) -> dict[str, Any]:
        """실제 P7 처리기의 자원(진단·GEX 알림 — 운영은 설정 파일·연결로 만든다)."""
        return {
            "repos": self.repos,
            "markets": self.markets(),
            "diag_config": load_diag(settings=self.sched_settings),
            "gex_config": load_gex(settings=self.sched_settings),
        }

    def _p5_resources(self) -> dict[str, Any]:
        """실제 P5 처리기의 자원(각 처리기 머리말 — 운영은 설정·연결로 만든다)."""
        cfg = load_macro(settings=self.sched_settings)
        gov = [x.item for x in cfg.series if x.dataset == "FRED:gov_series" and x.item]
        restricted = [
            x.item for x in cfg.series if x.dataset == "FRED:restricted_series" and x.item
        ]
        key = SecretStr(FRED_KEY)
        lim = StubLimiter()
        pub = self.repos.public()
        return {
            "repos": self.repos,
            "public_repos": pub,
            "kis": self.sched_kis,
            "markets": self.markets(),
            "macro_config": cfg,
            "us_config": load_us(),
            "fred": FredClient(
                key,
                limiter=lim,
                gov_series=gov,
                transport=self.fred.transport(),
                base_url="https://f",
            ),
            "fred_restricted": FredRestrictedClient(
                key,
                limiter=lim,
                restricted=restricted,
                transport=self.fred.transport(),
                base_url="https://f",
            ),
            "treasury": TreasuryClient(
                lim, transport=self.treasury.transport(), base_url="https://t"
            ),
            "nyfed": NyfedClient(lim, transport=self.nyfed.transport(), base_url="https://n"),
            "ff": FfClient(lim, transport=self.ff.transport(), base_url="https://ff"),
            "yahoo": YahooClient(
                lim, transport=self.yahoo.transport("scheduler"), base_url="https://y"
            ),
            "nasdaq": NasdaqClient(lim, transport=self.nasdaq.transport()),
            "stooq": StooqClient(lim, transport=self.stooq.transport()),
            "ecos": self.sched_ecos,
            "ecos_restricted": self.sched_ecos_restricted,
            "kosis": self.sched_kosis,
            "kofia": KofiaStatsClient(self.sched_datago),
            "fsc_stock": FscStockPriceClient(self.sched_datago),
            "fsc_index": FscIndexPriceClient(self.sched_datago),
            "public_connect": lambda: _PublicConn(self.public_log),
            "public_out_dir": self.public_out,
            "public_reader": MemReader(PubWorld(repos=pub, corp_code_rows=[])),
        }

    def _backup(self, now: datetime) -> Path:
        name = f"sim-backup-{now.astimezone(KST):%Y%m%d}.dump"
        self.backups.append(name)
        return Path(name)

    def restart_scheduler(self) -> None:
        """scheduler 프로세스 재기동.

        메모리(진행 중 시도·발화 창·토큰 리더)는 잃고 DB·Redis 는 남는다."""
        self.restarted_at.append(self.clock.now())
        set_default_client(None)
        self._scheduler_setup()

    # ── 시뮬레이션 처리기 ────────────────────────────────────────────────────────────────
    def _sim_handler(self, job: JobSpec) -> Handler:
        def run(ctx: JobContext) -> JobResult:
            got: list[DataKey] = []
            errors: list[str] = []
            for key in ctx.keys:
                try:
                    if self._fetch(job, key, ctx):
                        got.append(key)
                except Exception as e:  # 그 키만 실패로 — 처리기는 사유를 돌려준다
                    errors.append(f"{key.label()}: {type(e).__name__}")
            if job.notify is not None and job.notify.kind in SIM_NOTIFY_KINDS:
                d = date.fromisoformat(ctx.as_of[:10])
                ticket = self.notify_client.notify(
                    f"[시뮬레이션] {job.name} {ctx.as_of}",
                    kind=job.notify.kind,
                    source=f"sim:{job.name}",
                    as_of=d,
                )
                if not ticket.ok:
                    errors.append(f"notify: {ticket.reason}")
            if errors:
                return JobResult("failed", tuple(got), detail={"reason": errors[0]})
            if len(got) < len(ctx.keys):
                return JobResult("not_ready", tuple(got), detail={"reason": "아직 공표 전"})
            return JobResult("ok", tuple(got), detail={"sim": True})

        return run

    def _fetch(self, job: JobSpec, key: DataKey, ctx: JobContext) -> bool:
        """키 하나를 그 출처의 kbj 어댑터로 한 번 받는다. 공표 전이면 False."""
        if key.source in OFFLINE_SOURCES:
            self.offline_calls.append((job.name, key, ctx.now))
            return True
        if key.source == "KIS":
            return self._fetch_kis(job.name, key, self.sched_kis)
        if key.source == "KRX":
            rows = self.sched_krx.daily("/" + key.dataset, date.fromisoformat(key.as_of))
            return bool(rows)
        if key.source == "DART":
            return self._fetch_dart(key)
        if key.source == "DATAGO":
            return self._fetch_datago(key, job.name)
        if key.source == "ECOS":
            return self._fetch_ecos(key)
        if key.source == "KOSIS":
            return self._fetch_kosis(key)
        self.unsupported.append(key.label())
        raise SimUnsupported(key.label())

    def _fetch_kis(self, job: str, key: DataKey, client: KisRestClient) -> bool:
        spec = KIS_SPECS.get(key.dataset)
        if spec is None:
            self.unsupported.append(key.label())
            raise SimUnsupported(key.label())
        venue = VENUE_CODE[key.venue]
        day = key.as_of[:10].replace("-", "")
        for target in spec.per or ("",):
            resp = client.get(
                TRS[spec.tr_id], spec.tr_id, spec.params(venue, target, day), priority=spec.priority
            )
            if not resp.ok:
                raise RuntimeError(f"KIS {spec.tr_id} {resp.msg_cd}")
            self.kis_requests.append((job, key, target))
        return True

    def _fetch_dart(self, key: DataKey) -> bool:
        c = self.sched_dart
        if key.dataset == "list":
            ymd = key.as_of[:10].replace("-", "")
            c.get_json(
                "list.json",
                {"bgn_de": ymd, "end_de": ymd, "page_no": "1", "page_count": "100"},
                use_cache=False,
            )
            return True
        # P4(묶음 M 웨이브 1): 재무제표(fnlttSinglAcntAll·MultiAcnt)·구조화 공시·지분공시·
        # 주식총수·report_inputs 는 전부 event 데이터셋이라 실행기가 미리 잡지 않는다
        # (D-P4-25 ②) — 이 시뮬레이션 처리기에 오지 않는다. P4 의 non-event DART 키는
        # DART:list(minute)·DART:company(run_date) 둘뿐이다
        if key.dataset == "company":
            c.get_json("company.json", {"corp_code": "90000000"}, use_cache=False)
            return True
        self.unsupported.append(key.label())
        raise SimUnsupported(key.label())

    def _fetch_datago(self, key: DataKey, job: str = "") -> bool:
        if key.dataset in CUSTOMS_DATASETS:
            # 관세청(P6 — 작업 꺼짐). 매달 15~17일·1·11·21일에만 발화해 P2·P3 창에는 오지 않았고,
            # 가짜 공공데이터포털에 시군구·10일 잠정치 응답이 없다 — 부른 것만 기록한다(실제 수집은
            # P6 시험)
            self.offline_calls.append((job, key, self.clock.now()))
            return True
        d = date.fromisoformat(key.as_of[:10])
        t = self.sched_datago
        if key.dataset == "15094808":
            FscStockPriceClient(t).day(d)
            return True
        if key.dataset == "15094807":
            FscIndexPriceClient(t).index("코스피", d, d)
            return True
        if key.dataset.startswith("15094809/"):
            k = KofiaStatsClient(t)
            op = key.dataset.split("/", 1)[1]
            if op == "credit":
                rows = k.credit_balance(d, d)
            elif op == "capital":
                rows = k.market_capital(d, d)
            elif op == "cma":
                rows = k.cma(d, d)
            else:
                rows = k.fund_nav(d, "주식형", "공모")
            return bool(rows)
        self.unsupported.append(key.label())
        raise SimUnsupported(key.label())

    @staticmethod
    def _period(as_of: str) -> tuple[str, str]:
        """as_of → (ECOS 주기, 기간)."""
        if "Q" in as_of:
            return "Q", as_of
        if len(as_of) == 7:
            return "M", as_of.replace("-", "")
        return "D", as_of[:10].replace("-", "")

    def _fetch_ecos(self, key: DataKey) -> bool:
        cycle, p = self._period(key.as_of)
        if key.dataset in RESTRICTED_TABLES:
            require_restricted(key.dataset)
            rows = self.sched_ecos_restricted.search(key.dataset, cycle, p, p)
        else:
            rows = self.sched_ecos.search(key.dataset, cycle, p, p)
        return bool(rows)

    def _fetch_kosis(self, key: DataKey) -> bool:
        cycle, p = self._period(key.as_of)
        prd = {"M": "M", "Q": "Q", "D": "D"}[cycle]
        rows = self.sched_kosis.parameter_data(
            KOSIS_ORG, key.dataset, "ALL", "ALL", prd, start=p, end=p
        )
        return bool(rows)

    # gx(legacy GX external 단계 흉내) ───────────────────────────────────────────────────
    def _gx_setup(self) -> None:
        s = self.settings("gx")
        r = self._redis()
        self.gx_redis = r
        creds = KisCredentials.from_settings(s)
        self.gx_creds = creds
        self.gx_kis = make_client(
            self.kis, creds, r, consumer="gx", clock=self.clock, now=self.clock.now
        )
        self.gx_krx = KrxClient.from_settings(
            s,
            r,
            limits=self._limits(s),
            rate_clock=self.clock,
            transport=self.krx.transport("gx"),
            clock=self.clock.monotonic,
            now=self.clock.now,
        )
        self._gx_state: State | None = None
        self._gx_krx_due: dict[str, datetime] = {}  # as_of → 다음 시도 시각
        self._gx_poll_done: set[str] = set()
        self._gx_ws_done: set[tuple[date, str]] = set()
        # P7: KBJ GEX 상시 서비스(등록부 services 의 켜진 gex.*) — 하트비트는 프로세스 이름으로
        self.gex_redis = self._redis()
        self.gex_hbs: dict[str, Heartbeater] = {
            svc.name: Heartbeater(self.gex_redis, svc.beat_name, now=self.clock.now)
            for svc in self.registry.services
            if svc.enabled and svc.runner == "kbj" and svc.name.startswith("gex.")
        }
        self.gex_cycles: list[tuple[datetime, str, str]] = []  # (as_of, 세션, 결과 품질)
        self.gex_engine: EngineService | None = None
        if self.opts.p7:
            self._gex_engine_setup()

    def _gex_engine_setup(self) -> None:
        """실제 GEX 엔진 — 메모리 GEX 저장소를 읽고 쓴다(운영 PgGexRepo 자리)."""
        seed_master(self.gex_redis, "\n".join(P7_CHAIN.master_lines()) + "\n")
        t0 = self.start
        d0 = t0.astimezone(KST).date()
        self.repos.gex.seed_expiries(
            [
                ExpiryRecord(
                    ts=t0, trade_date=d0, session="day", mrkt_cls="", expiry=P7_MONTH,
                    last_trade_date=P7_EXPIRY, source="kis",
                )
            ]
        )  # fmt: skip
        self.gex_engine = EngineService(
            self.repos.gex,
            self.repos.gex,
            EnginePublisher(self.gex_redis),
            EngineContext(self.gex_redis, self.kr, clock=self.clock.monotonic),
            self.kr,
            health=MemoryHealthSink(),
            now=self.clock.now,
            mono=self.clock.monotonic,
            heartbeat=Heartbeater(self.gex_redis, ENGINE_SERVICE, now=self.clock.now),
        )

    def _gex_cycle(self, now: datetime) -> None:
        """poller 흉내 회차 하나 → 합성 체인·선물 행을 저장소에 두고 chain.ready 한 건(실제
        엔진)."""
        eng = self.gex_engine
        if eng is None:
            return
        info = state_at(now, self.kr)
        if info.trade_date is None or info.session is None:
            return
        src: Literal["board", "fill"] = "board" if info.session == "day" else "fill"
        self.repos.gex.seed_chain(synthetic_series("", P7_MONTH, P7_EXPIRY, now, source=src))
        self.repos.gex.seed_futures(
            [
                fut_row(
                    now - timedelta(seconds=5), remaining_days=5, session=info.session,
                    trade_date=info.trade_date,
                )
            ]
        )  # fmt: skip
        res = eng.on_ready(
            ChainReady(
                ts=now, trade_date=info.trade_date, session=info.session,
                series=(("", P7_MONTH),),
            )
        )  # fmt: skip
        self.gex_cycles.append((now, info.session, "none" if res is None else res.quality))

    def _gx_claim(self, job: str, key: DataKey, now: datetime) -> bool:
        return self.claims.claim(key, job, make_run_id(job, key.as_of, 1), now)

    def _gx_tick(self, now: datetime) -> None:
        info: SessionInfo | None = self.scheduler.info
        if info is None:
            return
        local = now.astimezone(KST)
        d = local.date()
        trading = self.kr.is_trading_day(d)
        prev, cur = self._gx_state, info.state
        self._gx_state = cur
        if prev is not None and prev != cur:
            if cur in (State.PRE_DAY, State.PRE_NIGHT) and trading:
                # GX 는 세션마다 마스터를 받는다. 야간 세션의 귀속 거래일은 다음 거래일(GX state_at)
                as_of = d if cur == State.PRE_DAY else self.kr.next_trading_day(d)
                self._gx_master(now, as_of.isoformat())
            if cur == State.POST_DAY and trading:
                self._gx_minutes(now, "gex.day_minutes", "fut_minute_day", "F", d)
            if cur == State.IDLE and prev == State.NIGHT and info.trade_date is None:
                # 야간 세션은 다음 거래일 귀속(GX state_at) — 06:00 에 끝난 그 세션의 거래일
                night_day = d if trading else self.kr.next_trading_day(d)
                self._gx_minutes(now, "gex.night_minutes", "fut_minute_night", "CM", night_day)
        if trading and local.time() >= dtime(8, 5):
            self._gx_krx_derivatives(now, d)
        if cur in (State.DAY, State.NIGHT) and local.minute % 10 == 0:
            self._gx_poll(now)
            self._gex_cycle(now)
        if self.ws is not None and trading:
            for at, label in ((dtime(8, 44), "day"), (dtime(17, 59), "night")):
                if label == "night" and not night_session_opens(d, self.kr):
                    continue
                if local.time() >= at and (d, label) not in self._gx_ws_done:
                    self._gx_ws_done.add((d, label))
                    self._gx_ws(now)

    def _gx_master(self, now: datetime, as_of: str) -> None:
        key = self.catalog["KIS:fo_master"].key(as_of)
        if not self._gx_claim("kis.master", key, now):
            self.external_runs.append(ExternalRun("kis.master", as_of, now, "duplicate"))
            return
        rid = make_run_id("kis.master", as_of, 1)
        try:
            rows = parse_master_zip(
                download_fo_master(
                    transport=self.kis.master_transport("gx"), clock=self.clock.monotonic
                )
            )
        except Exception as e:
            self.claims.fail(key, rid, type(e).__name__, now)
            self.external_runs.append(ExternalRun("kis.master", as_of, now, "failed", str(e)))
            return
        self.claims.complete(key, rid, len(rows), self.clock.now())
        self.external_runs.append(ExternalRun("kis.master", as_of, now, "ok"))

    def _gx_minutes(self, now: datetime, job: str, dataset: str, market: str, d: date) -> None:
        key = self.catalog[f"KIS:{dataset}"].key(d.isoformat())
        if not self._gx_claim(job, key, now):
            self.external_runs.append(ExternalRun(job, key.as_of, now, "duplicate"))
            return
        rid = make_run_id(job, key.as_of, 1)
        try:
            resp = self.gx_kis.get(
                MINUTE_PATH,
                MINUTE_TR,
                minute_chart_params(market, FUT_CODE, d, "154500"),
                priority=Priority.P4,
            )
            why = "" if resp.ok else resp.msg_cd
        except Exception as e:  # 리미터(Redis)·토큰 없음 — 그 단계만 실패(GX 도 다음 진입에 다시)
            why = type(e).__name__
        if why:
            self.claims.fail(key, rid, why, self.clock.now())
            self.external_runs.append(ExternalRun(job, key.as_of, now, "failed", why))
            return
        self.claims.complete(key, rid, None, self.clock.now())
        self.external_runs.append(ExternalRun(job, key.as_of, now, "ok"))

    def _gx_krx_derivatives(self, now: datetime, d: date) -> None:
        """GX KrxDaily 흉내 — 08:05 부터 10분마다 10:00 까지(공표 전이면 다시)."""
        as_of = self.kr.prev_trading_day(d).isoformat()
        due = self._gx_krx_due.get(as_of, kst(d, dtime(8, 5)))
        if now < due or now.astimezone(KST).time() > dtime(10, 0):
            return
        keys = [
            self.catalog[f"KRX:{ds}"].key(as_of, v)
            for ds in ("drv/fut_bydd_trd", "drv/opt_bydd_trd")
            for v in self.catalog[f"KRX:{ds}"].venues or (None,)
        ]
        live = [k for k in keys if (row := self.claims.live(k)) is None or row.status != "done"]
        if not live:
            self._gx_krx_due[as_of] = datetime.max.replace(tzinfo=KST)
            return
        rid = make_run_id("gex.krx_derivatives", as_of, 1)
        pending = False
        for k in live:
            if not self._gx_claim("gex.krx_derivatives", k, now):
                self.external_runs.append(
                    ExternalRun("gex.krx_derivatives", as_of, now, "duplicate")
                )
                continue
            try:
                rows = self.gx_krx.daily("/" + k.dataset, date.fromisoformat(as_of))
            except Exception as e:  # 그 시도만 실패 — 쥔 키는 다음 시도가 이어 쓴다
                rows = []
                self.external_runs.append(
                    ExternalRun("gex.krx_derivatives", as_of, now, "failed", type(e).__name__)
                )
            if rows:
                self.claims.complete(k, rid, len(rows), self.clock.now())
            else:
                pending = True  # 같은 작업이 쥔 채로 다음 시도에 이어 쓴다
        status = "not_ready" if pending else "ok"
        self.external_runs.append(ExternalRun("gex.krx_derivatives", as_of, now, status))
        self._gx_krx_due[as_of] = now + timedelta(minutes=10)

    def _gx_poll(self, now: datetime) -> None:
        """GX poller 흉내 — 세션 중 10분마다 한 번(실제는 초 단위 — 리미터 P1).

        P5(docs/p5_design.md R16·D-P5-21 ①): 장중 투자자 1분(KIS:market_investor_intraday)은
        등록부에서
        flows.market_minute 이 유일하게 수집한다(ADR 0019) — poller 흉내는 이 키를 더 잡지
        않는다."""
        minute = now.astimezone(KST).strftime("%Y-%m-%dT%H:%M")
        if minute in self._gx_poll_done:
            return
        self._gx_poll_done.add(minute)
        for dataset, tr_id, prio, params in (
            (
                "option_chain_live",
                "FHPIF05030100",
                Priority.P1,
                {"FID_COND_MRKT_DIV_CODE": "O", "FID_INPUT_ISCD": "", "FID_MTRT_CNT": "202610"},
            ),
        ):
            key = self.catalog[f"KIS:{dataset}"].key(minute)
            if not self._gx_claim("gex.poller", key, now):
                self.external_runs.append(ExternalRun("gex.poller", minute, now, "duplicate"))
                continue
            rid = make_run_id("gex.poller", minute, 1)
            try:
                resp = self.gx_kis.get(TRS[tr_id], tr_id, params, priority=prio)
            except Exception as e:  # 리미터(Redis)·토큰 없음 — 그 회차만 실패
                self.claims.fail(key, rid, type(e).__name__, self.clock.now())
                self.external_runs.append(
                    ExternalRun("gex.poller", minute, now, "failed", type(e).__name__)
                )
                continue
            if resp.ok:
                self.claims.complete(key, rid, None, self.clock.now())
                self.external_runs.append(ExternalRun("gex.poller", minute, now, "ok"))
            else:
                self.claims.fail(key, rid, resp.msg_cd, self.clock.now())
                self.external_runs.append(
                    ExternalRun("gex.poller", minute, now, "failed", resp.msg_cd)
                )

    def _gx_ws(self, now: datetime) -> None:
        """ws-gateway 흉내 — auth 가 둔 접속키를 읽어(발급 없음) 등록·해제 한 번."""
        assert self.ws is not None
        minute = now.astimezone(KST).strftime("%Y-%m-%dT%H:%M")
        key = self.catalog["KIS:ws_ticks"].key(minute)
        if not self._gx_claim("gex.ws_gateway", key, now):
            self.external_runs.append(ExternalRun("gex.ws_gateway", minute, now, "duplicate"))
            return
        approval = reader(self.gx_redis, self.gx_creds, "ws_key", now=self.clock.now, by="gx").get()
        sub, unsub = subscribe_once(self.ws.url, approval, "H0IFCNT0", "101W12")
        self.ws_results.append((now, sub, unsub))
        self.claims.complete(key, make_run_id("gex.ws_gateway", minute, 1), None, self.clock.now())
        self.external_runs.append(ExternalRun("gex.ws_gateway", minute, now, "ok"))

    # legacy(SD·ET 가 브리지로 부르는 KIS) ─────────────────────────────────────────────
    def _legacy_setup(self) -> None:
        s = self.settings("legacy")
        r = self._redis()
        creds = KisCredentials.from_settings(s)
        legacy_bridge.reset()
        legacy_bridge.configure(
            settings=s,
            redis=r,
            now=self.clock.now,
            kis=make_client(
                self.kis, creds, r, consumer="legacy", clock=self.clock, now=self.clock.now
            ),
            master=lambda: download_fo_master(transport=self.kis.master_transport("legacy")),
        )
        self._legacy_done: set[date] = set()

    def _legacy_tick(self, now: datetime) -> None:
        if not self.opts.legacy_calls:
            return
        local = now.astimezone(KST)
        d = local.date()
        if d in self._legacy_done or not self.kr.is_trading_day(d) or local.time() < dtime(10, 30):
            return
        self._legacy_done.add(d)
        self.legacy_quote()

    def legacy_quote(self) -> int:
        """legacy SD `kis_api` 모양의 현재가 조회 한 번(브리지). HTTP 상태를 돌려준다."""
        now = self.clock.now()
        token = legacy_bridge.access_token_or_none()
        # 호출자가 옛 토큰·앱키를 실어도 브리지가 버리고 KBJ 값을 넣는다
        resp = legacy_bridge.get(
            "kis:" + TRS["FHKST01010100"],
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": SYMBOLS[0]},
            headers={
                "tr_id": "FHKST01010100",
                "authorization": "Bearer legacy-stale-token",
                "appkey": "legacy-old-appkey",
            },
        )
        self.legacy_results.append((now, resp.status_code, token))
        return resp.status_code

    # ── 진행 ─────────────────────────────────────────────────────────────────────────────
    def _transitions(self, d: date) -> list[datetime]:
        return [
            kst(d, t)
            for t in (PRE_DAY_START, DAY_START, DAY_END, PRE_NIGHT_START, NIGHT_START, NIGHT_END)
        ]

    def _step_after(self, cur: datetime) -> datetime:
        local = cur.astimezone(KST)
        near = any(
            abs((t - cur).total_seconds()) <= FINE_AROUND_S
            for dd in (
                local.date() - timedelta(days=1),
                local.date(),
                local.date() + timedelta(days=1),
            )
            for t in self._transitions(dd)
        )
        step = FINE_S if near else STEP_S
        epoch = int(cur.timestamp())
        nxt = (epoch // step + 1) * step
        return datetime.fromtimestamp(nxt, tz=cur.tzinfo).astimezone(cur.tzinfo)

    def _drain_events(self) -> None:
        misses = 0
        for _ in range(1000):
            try:
                m = self._observer.get_message()
            except RedisError:
                return
            if m is None:
                misses += 1
                if misses >= 2:
                    return
                continue
            data = m.get("data")
            if isinstance(data, bytes):
                self.session_events.append(json.loads(data))

    def tick(self, now: datetime) -> None:
        self.ticks += 1
        down = self.opts.redis_down
        self.redis_server.connected = not (down is not None and down[0] <= now < down[1])
        while self._hooks and self._hooks[0][0] <= now:
            _, fn = self._hooks.pop(0)
            fn(self)
        while self._restarts and self._restarts[0] <= now:
            self._restarts.pop(0)
            self.restart_scheduler()
        self._auth_tick(now)
        self.scheduler.step()
        self.sched_hb.beat()
        self._gx_tick(self.clock.now())
        # ws-gateway·recorder(·엔진 사이클이 없는 창의 engine) 하트비트
        for name, hb in self.gex_hbs.items():
            if not (name == "gex.engine" and self.gex_engine is not None):
                hb.beat()
        if self.gex_engine is not None:
            self.gex_engine.tick()  # 곁일 + 하트비트(실제 엔진)
        self._legacy_tick(self.clock.now())
        self.notifier.step()
        self.notifier_hb.beat()
        self._drain_events()

    def run(self) -> SimDay:
        t0 = time.monotonic()
        while True:
            now = self.clock.now()
            if now >= self.end:
                break
            self.tick(now)
            nxt = self._step_after(max(now, self.clock.now()))
            if nxt > self.clock.now():
                self.clock.set(nxt)
        self.redis_server.connected = True
        self.notifier.step()  # 마지막 tick 에 들어온 알림까지 처리
        self.wall_s = time.monotonic() - t0
        return self

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        set_default_client(None)
        legacy_bridge.reset()
        if self.ws is not None:
            self.ws.stop()
        self.auth_http.close()
        shutil.rmtree(self.public_out, ignore_errors=True)

    # ── 결과 보기(시험 편의) ────────────────────────────────────────────────────────────
    def runs_of(self, job: str) -> list[RunRecord]:
        return self.runs.of(job)

    def final_runs(self) -> dict[tuple[str, str], RunRecord]:
        """(작업, as_of) → 마지막 시도 기록(씨앗 기록 제외)."""
        out: dict[tuple[str, str], RunRecord] = {}
        for r in self.runs.records.values():
            if r.detail.get("seeded"):
                continue
            cur = out.get((r.job, r.as_of))
            if cur is None or r.attempt > cur.attempt:
                out[(r.job, r.as_of)] = r
        return out

    def done_keys(self) -> Counter[DataKey]:
        return Counter(r.key for r in self.claims.rows if r.status == "done")

    def jobs_per_dataset(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for r in self.claims.rows:
            out.setdefault(r.key.dataset_id, set()).add(r.job)
        return out

    def budget_used(self, source: str, day: date, *, scope: str | None = None) -> int:
        """그날 쓴 일 예산(Redis 카운터). KRX 는 GX 와 같은 `krx:calls:<날짜>`."""
        at = kst(day, dtime(12, 0))
        if source == "krx":
            return krx_budget(self.sched_redis, 8000).used(at)
        cap = {"dart": 18000, "datago": 9500}[source]
        return DailyBudget(self.sched_redis, source, cap, scope=scope).used(at)

    def health_kinds(self, severity: str | None = None) -> list[str]:
        out: list[str] = []
        for sink in (self.auth_health, self.sched_health, self.notifier_health):
            out += [e.kind for e in sink.events if severity is None or e.severity == severity]
        return out


def kis_by_consumer(sim: SimDay) -> Mapping[str, int]:
    return Counter(c.consumer for c in sim.kis.calls)
