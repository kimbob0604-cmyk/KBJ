"""KIS 논리 데이터셋 — 작업 등록부·선점(claim)·카탈로그가 쓰는 메타데이터(설계 §2·§6.7,
docs/metrics.md).

등급은 모두 **로그인**(DATA_TIERS §1 — KIS 시세)이라 저장 표는 `prv_*` 뿐이다. 같은 TR 이라도 쓰임이
다르면 다른 데이터셋이다(예: `stock_quote_eod` 대 `watch_quotes_intraday`) — 같은 `(source,
dataset)` 은 등록부 전체에서 한 작업만 받는다(설계 §6.5). 수집 작업 자체는 P3
이후(`docs/p2_design.md` §6.7).

- TR·경로·필드는 ET `board/ingest/kis.py:ENDPOINTS`, SD `kis_api.py`, GX `scripts/probe_common.py`·
  `services/poller/endpoints.py` 에서 모았다. 실측하지 않은 것은 notes 에 **[실측 필요]**,
  기본값으로 정한 것은 **[확인 필요]**(`docs/probe_results.md` §7 14번 체크리스트).
- **거래소 구분**(docs/metrics.md §1): 거래대금·순매수는 KRX 와 넥스트레이드(NXT)를 나눠 저장하고
  합계를 따로 둔다. 거래소별로 받을 수 있는 데이터셋은 `venues` 에 KRX·NXT·TOTAL(통합 응답)을 적어
  데이터 키에 거래소가 들어가게 한다. KIS 의 거래소 구분 파라미터(`FID_COND_MRKT_DIV_CODE` J·NX·UN
  으로 추정)와 응답이 통합인지 KRX 만인지는 **[실측 필요]** — 실측 전에는 받은 그대로의 구분으로
  키를 만들고 합계는 계산하지 않는다.
- 리미터는 모두 앱키 하나(`limits.yaml` 의 `kis` — `rl:kis:<앱키 해시>`). KIS 는 일 한도를 공표하지
  않아 예산(budget)은 없다.
- 이 모듈은 문자열 메타데이터뿐이다. 카탈로그(`kbj/data/catalog.py`, 묶음 F)가 모은다.
- **KIS 컨센서스 TR id·경로는 `CONSENSUS_TRS` 한 곳**(docs/p4_design.md D-P4-9·§9.3, ADR
  0015 2-1 — 메인 결정 2026-10-08). 레포 안의 옛 값이 서로 어긋났다(이 파일의 옛 노트·
  conflict_map §1.13·가짜 서버). 파서(`kbj.data.private.kis.consensus`)·가짜 서버
  (`tests/fakes/kis_server.py`)·시뮬레이션(`tests/sim/harness.py`)·시험은 이 상수를 import 하고
  문자열을 다시 쓰지 않는다(`tests/unit/kis/test_consensus_tr_single.py` 가 지킨다). 실측
  (probe_results §7 #29) 뒤에는 이 상수 하나만 고친다.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from kbj.data.spec import AsOfKind, DatasetSpec, Tier, Venue

SOURCE: Final = "KIS"
LIMITER: Final = "kis"
_SPLIT: Final = (Venue.KRX, Venue.NXT, Venue.TOTAL)

# KIS 컨센서스 TR — 데이터셋 이름 → (tr_id, 경로). **전부 [확인 필요 #29]**(기억에 기댄 값 —
# 실측 전).
# - consensus_estimate: 국내주식 종목추정실적(결산년월별 매출·영업이익·순이익·EBITDA·EPS). 여러
#   증권사 평균인지 한국투자증권 자체 추정인지 모른다(ADR 0015 §1 — 자체 추정이면 화면 이름
#   "KIS 추정").
# - invest_opinion: 국내주식 종목투자의견(종목 하나의 증권사별 의견·목표가, 기간 질의).
# - invest_opinion_by_broker: 국내주식 증권사별 투자의견(증권사 하나의 종목들) — 대안. P4 는 부르지
#   않는다(등록부 collects 에 없음 — 종목 단위 invest_opinion 이 기본).
CONSENSUS_TRS: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "consensus_estimate": (
            "HHKST668300C0",
            "/uapi/domestic-stock/v1/quotations/estimate-perform",
        ),
        "invest_opinion": (
            "FHKST663300C0",
            "/uapi/domestic-stock/v1/quotations/invest-opinion",
        ),
        "invest_opinion_by_broker": (
            "FHKST663400C0",
            "/uapi/domestic-stock/v1/quotations/invest-opbysec",
        ),
    }
)


def _tr_note(dataset: str) -> str:
    """카탈로그 노트의 TR 문구 — 상수에서 만든다(문자열을 다시 쓰지 않는다)."""
    tr_id, path = CONSENSUS_TRS[dataset]
    return f"{tr_id}({path}) [확인 필요 #29 — 상수 CONSENSUS_TRS]"


def _kis(
    dataset: str,
    *,
    published: str,
    as_of: AsOfKind,
    store: str,
    notes: str,
    venues: tuple[Venue, ...] = (),
) -> DatasetSpec:
    return DatasetSpec(
        id=f"{SOURCE}:{dataset}",
        source=SOURCE,
        dataset=dataset,
        tier=Tier.PRIVATE,
        limiter=LIMITER,
        budget=None,
        published=published,
        as_of_kind=as_of,
        store=store,
        notes=notes,
        venues=venues,
    )


DATASETS: Final[tuple[DatasetSpec, ...]] = (
    # ── 장 마감 수집(market.close_collect — P3, ADR 0001 Q1 결정 전 기본값) ────────────────────
    _kis(
        "stock_quote_eod",
        published="정규장 마감(15:30 KST) 뒤 당일 잠정 — 다음 거래일 KRX 일별(stk_bydd_trd)로 대조",
        as_of="trade_date",
        store="prv_market.stock_snapshot",
        notes=(
            "FHKST01010100 주식현재가 시세(/uapi/domestic-stock/v1/quotations/inquire-price). "
            "종가·등락률·거래량·거래대금(acml_tr_pbmn)·시가총액. 거래대금에 시간외가 드는지 "
            "[실측 필요]. 거래소 구분 [실측 필요: J·NX·UN]"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "stock_investor_daily",
        published="장 마감 뒤 당일 확정치 반영 [실측 필요: 반영 시각]",
        as_of="trade_date",
        store="prv_flows.stock_investor_daily",
        notes=(
            "FHKST01010900 종목별 투자자(/uapi/domestic-stock/v1/quotations/inquire-investor). "
            "개인·외국인·기관 순매수 수량·금액(prsn_ntby_tr_pbmn·frgn_ntby_tr_pbmn·"
            "orgn_ntby_tr_pbmn — 금액 = 실거래대금), 최근 30영업일. 기관 7구분은 이 TR 에 없다 "
            "— FHPTJ04160001 [추정·실측 필요](conflict_map 후보 A). 장중 가집계(inst_foreign_*)를 "
            "마감 뒤 이 값으로 덮어쓰고 차이를 남긴다(metrics §2). 거래소 구분 [실측 필요]"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "market_investor_daily",
        published="장 마감 뒤 [실측 필요: 반영 시각]",
        as_of="trade_date",
        store="prv_flows.market_investor_daily",
        notes=(
            "FHPTJ04040000 시장별 투자자매매동향 일별"
            "(/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market, output1). "
            "코스피·코스닥 투자자별 순매수 금액. 거래소 구분 [실측 필요]"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "inst_foreign_top",
        published="장 마감 무렵 가집계 스냅(증권사 추정치) — quality=estimated",
        as_of="trade_date",
        store="prv_flows.stock_investor_daily",
        notes=(
            "FHPTJ04400000 국내기관·외국인 매매종목 가집계"
            "(/uapi/domestic-stock/v1/quotations/foreign-institution-total). 추정치라 "
            "quality=estimated·source 'kis.prelim' 으로 일별 원장에 넣고 KIS:stock_investor_daily "
            "확정치로 덮어쓴다(metrics §2, D-P3-7 — 차이는 prv_flows.investor_revision). "
            "상위 몇 종목까지·정렬 옵션 [실측 필요]. 저장 표 확정(P3 — docs/p3_design.md §3.3)"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "etf_investor_daily",
        published="장 마감 뒤 당일 확정치 반영 [실측 필요: 반영 시각]",
        as_of="trade_date",
        store="prv_flows.stock_investor_daily",
        notes=(
            "FHKST01010900 종목별 투자자를 ETF 코드로 [실측 필요: ETF 에 되는지·금액 단위]. "
            "투자자별 ETF 순매수(장내 — 상대가 대부분 LP 라 순유입과 합치지 않고 따로, "
            "metrics §4). "
            "순자산 하한 config/markets.yaml etf.investor_min_net_asset_krw(1,000억 [확인 필요]) "
            "이상 ETF 만. 작업 market.close_collect(P3). 거래소 구분 [실측 필요]"
        ),
        venues=_SPLIT,
    ),
    # ── 장중(잠정) — 순위 화면·상단 띠(P3~P5), 알림(P8) ─────────────────────────────────────────
    _kis(
        "inst_foreign_intraday",
        published="장중 가집계 — 증권사 추정치(quality=estimated), 화면에 '잠정' 표시",
        as_of="slot10m",
        store="prv_flows.investor_intraday",
        notes=(
            "FHPTJ04400000 장중 회차(같은 TR 이 장 마감 스냅 inst_foreign_top 과 쓰임이 달라 "
            "따로 둔다). 슬롯마다 prv_flows.investor_intraday(이력)에 쌓고, 일별 원장 "
            "prv_flows.stock_investor_daily 의 오늘 행(source 'kis.prelim')도 갱신한다. "
            "가집계 갱신 주기·회차·상위 개수 [실측 필요]. 마감 뒤 확정치로 덮어쓴다(D-P3-7)"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "turnover_rank_intraday",
        published="장중 실시간 순위(잠정) — 확정은 다음 거래일 KRX 일별",
        as_of="slot10m",
        store="prv_market.turnover_rank_intraday",
        notes=(
            "FHPST01710000 거래량 순위(/uapi/domestic-stock/v1/quotations/volume-rank). "
            "거래대금 정렬(FID_BLNG_CLS_CODE 거래금액순 추정)·상위 몇 개까지 [실측 필요]. "
            "슬롯마다 prv_market.turnover_rank_intraday(이력)에 쌓고 목록 종목의 오늘 스냅"
            "(prv_market.stock_snapshot — turnover_is_estimate, quality=estimated)도 갱신. "
            "시장 거래대금(코스피+코스닥)과 ETF·ETN 은 따로 집계(metrics §1)"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "etf_quote_intraday",
        published="장중 — iNAV·괴리율(경고용, 확정 NAV 는 KRX:etp/etf_bydd_trd)",
        as_of="slot10m",
        store="prv_etf.quote_intraday",
        notes=(
            "FHPST02400000 ETF/ETN 현재가(/uapi/etfetn/v1/quotations/inquire-price). "
            "장중 NAV(nav)·괴리율·거래대금 필드 이름 [실측 필요]. 괴리율 경고에만 쓴다"
            "(metrics §4 — 순유입 계산은 마감 NAV 만). 표는 0009(P3 로 당김 — D-P3-12). "
            "대상 = 순자산 상위 config/markets.yaml etf.watch_top_n(50 [확인 필요]). "
            "ETF 의 NXT 거래 여부 [실측 필요] — 지금은 거래소를 나누지 않는다"
        ),
    ),
    _kis(
        "index_quote_intraday",
        published="장중 10분마다(09:00~15:30 KST) — 지수 현재가·누적 거래대금(잠정)",
        as_of="slot10m",
        store="prv_market.index_intraday",
        notes=(
            "[추정 TR] FHPUP02100000 국내업종 현재지수(conflict_map §1.13) [실측 필요: TR·필드]. "
            "코스피(0001)·코스닥(1001)·코스피200(2001) [실측 필요: 코드]. 누적 거래대금으로 장중 "
            "시장 거래대금(지수 기준, estimated — ETF 포함 여부 [실측 필요], "
            "docs/p3_design.md §4.4). "
            "작업 market.intraday(P3). 지수라 거래소를 나누지 않는다"
        ),
    ),
    _kis(
        "sector_quote_intraday",
        published="장중 10분마다(09:00~15:30 KST) — 업종 지수(잠정)",
        as_of="slot10m",
        store="prv_market.sector_intraday",
        notes=(
            "[추정 TR] FHPUP02140000 업종 구분별 전체시세 [실측 필요: TR·필드·업종 코드]. "
            "시장(코스피·코스닥)마다 1회 호출로 전 업종. 페이지 1 업종 히트맵(장중). "
            "작업 market.intraday(P3)"
        ),
    ),
    _kis(
        "watch_quotes_intraday",
        published="장중 10분마다(09:00~15:30 KST)",
        as_of="slot10m",
        store="",
        notes=(
            "FHKST01010100 관심종목 현재가 — 알림 규칙(rules.intraday, P8)이 평가에만 쓴다. "
            "저장 여부는 P8 [확인 필요]"
        ),
    ),
    _kis(
        "quote_on_demand",
        published="명령 응답(/가격) 즉석 조회 — 선점 대상 아님(리미터는 탄다, P2 우선순위)",
        as_of="event",
        store="",
        notes="FHKST01010100 단건 현재가. 저장하지 않는다(설계 §6.5 끝)",
    ),
    # ── 컨센서스(P4 — 묶음 K, docs/p4_design.md §3.3·§4.7, ADR 0015) ──────────────────────────
    # 작업 consensus.snapshot(16:00 — ADR 0018)이 두 데이터셋을 함께 받는다. 실데이터 확인(#29)
    # 전까지 등록부에서 꺼져 있다(D-P4-10). 유니버스 = 관심종목 ∪ 전 거래일 시총 상위 top_n
    # 보통주.
    _kis(
        "consensus_estimate",
        published="장 마감 뒤 [실측 필요 #29: 갱신 시각]",
        as_of="trade_date",
        store="prv_fin.consensus_snapshot",
        notes=(
            f"[추정 TR] 종목추정실적 {_tr_note('consensus_estimate')}. 결산년월별 실적·추정 구분, "
            "매출·영업이익·순이익·EBITDA·EPS — 출력 이름·금액 단위(억원?)·순이익 기준(지배?)·"
            "시장 컨센서스 여부 [실측 필요 #29]. 빈 응답은 KisEmpty(0 으로 채우지 않는다). 파서 "
            "kbj.data.private.kis.consensus"
        ),
    ),
    _kis(
        "invest_opinion",
        published="장 마감 뒤 [실측 필요 #29: 갱신 시각]",
        as_of="trade_date",
        store="prv_fin.invest_opinion",
        notes=(
            f"[추정 TR] 종목투자의견 {_tr_note('invest_opinion')}. 종목 하나의 증권사별 의견·직전 "
            "의견·목표가, 기간 질의(config/fin.yaml consensus.opinion_window_days — 90일). 대안 "
            f"증권사별 투자의견 {_tr_note('invest_opinion_by_broker')} 은 부르지 않는다. 출력 이름·"
            "의견 문구 [실측 필요 #29]. 목표가 컨센서스는 우리가 계산(kbj.engines.consensus.target)"
        ),
    ),
    # ── GEX(legacy GX — P7 까지 runner: external) ──────────────────────────────────────────
    _kis(
        "fo_master",
        published="거래일 PRE_DAY·PRE_NIGHT 진입 때(배포 파일 — GX 실측)",
        as_of="trade_date",
        store="prv_gex.master_snapshots",
        notes=(
            "지수선물옵션 마스터 zip(fo_idx_code_mts.mst.zip, kbj.data.private.kis.master). 앱키 "
            "REST 가 아니라 배포 서버라 리미터를 타지 않는다(limiter 칸은 출처 표시)"
        ),
    ),
    _kis(
        "fut_minute_day",
        published="주간 세션 마감 뒤(POST_DAY 16:00~17:50 KST)",
        as_of="trade_date",
        store="prv_gex.minute_bars",
        notes="FHKIF03020200 선물옵션 분봉(market F) — GX MinuteDaily·GapDaily(P7)",
    ),
    _kis(
        "fut_minute_night",
        published="야간 세션 마감 뒤(IDLE 06:10~08:00 KST)",
        as_of="trade_date",
        store="prv_gex.minute_bars",
        notes="FHKIF03020200 선물옵션 분봉(market CM) — 귀속 거래일 기준(GX state_at)",
    ),
    _kis(
        "option_chain_live",
        published="세션 중 연속(GX poller 주기)",
        as_of="minute",
        store="prv_gex.chain_snapshots",
        notes=(
            "FHPIF05030100 전광판 콜/풋(1초 간격)·FHPIO056104C0 월물리스트·FHPIF05030200 선물 "
            "전광판·FHMIF10000000 단건 — GX poller(P7)"
        ),
    ),
    _kis(
        "market_investor_intraday",
        published="호출 시점의 오늘 누적 한 줄(분 이력 아님 — 1분마다 불러 쌓는다, F2 §1.1)",
        as_of="minute",
        store="prv_gex.investor_flow",
        notes=(
            "FHPTJ04030000 시장별 투자자매매동향(시세성)"
            "(/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market) — 입력 "
            "FID_INPUT_ISCD(시장)·FID_INPUT_ISCD_2(업종). 쓰기 주인은 P5 flows.market_minute 하나"
            "(ADR 0019 — 등록부에서 gex.poller 의 collects 를 옮겼다, D-P5-21 ①). 투자자 12구분 × "
            "매도·매수·순매수 × 수량·대금(백만원 — GX 단위). 거래소 구분 입력 없음(KIS 기본 — "
            "KRX 로 표시). 현물 코드 쌍·주식선물 코드·장전 값·통합 여부·etc_orgt 가 기관 합계에 "
            "드는지 [확인 필요 #39] — 실측 전에는 GX 가 검증한 K2I F001·OC01·OP01 만 켠다"
            "(config/markets.yaml flows.minute.pairs). prv_flows.market_investor_intraday 는 이 "
            "표의 뷰(0006). 파서 kbj/data/private/kis/investors_minute.py(TR_MARKET_MINUTE·칸 표 — "
            "순매수 수량 칸 pe_fund·etc_orgt·etc_corp 는 _ntby_vol, 나머지 _ntby_qty: GX 실측). "
            "행 ts = 슬롯 분, 슬롯 + 45초 넘어 받으면 estimated, 값이 모두 0 이면 공표 전(장전 — "
            "#39). P7: 위클리 4조합(WKM OC05·OP05, WKI OC04·OP04)은 GX 실측 #13(7조합 정상 "
            "응답)을 근거로 켠다(WRAP — markets.yaml), 야간(K2I F001 night)은 응답 실측 #P7-2 "
            "전까지 꺼진 채(D-P7-6)"
        ),
    ),
    # ── P5 F2 프로그램매매(docs/p5_flow_program_prompt.md §1.4 — 칸 이름은 KIS 공식 예제 기준,
    # 단위·반영 시각 [실측 필요 #40·#41]). TR·입력 칸·응답 칸 이름·단위 상수는 파서
    # kbj/data/private/kis/program.py 한 곳(PROGRAM_TRS·market_field·stock_field·INVESTOR_CODES·
    # MARKET_VALUE_UNIT_KRW 백만원 가정·STOCK_VALUE_UNIT_KRW 원 가정 — 전부 [확인 필요]). 가짜 KIS·
    # 시뮬레이션·시험은 그 표를 import 한다. 금액은 원으로 바꿔 저장.
    _kis(
        "program_market_daily",
        published="장 마감 뒤 당일 확정 [확인 필요 #40: 반영 시각]",
        as_of="trade_date",
        store="prv_flows.program_market_daily",
        notes=(
            "FHPPG04600001 프로그램매매 종합현황(일별)"
            "(/uapi/domestic-stock/v1/quotations/comp-program-trade-daily) — 입력 "
            "FID_COND_MRKT_DIV_CODE(J·NX·UN)·FID_MRKT_CLS_CODE(K 코스피·Q 코스닥)·시작·끝 날짜. "
            "응답 차익 arbt_*·비차익 nabt_*·전체 whol_* × 위탁 entm·자기 onsl·합계 smtn × "
            "매도·매수2·"
            "순매수 × 수량·대금. 대금 단위·날짜 구간 상한(60일 백필 호출 수) [확인 필요 #40]. "
            "작업 program.daily(close+20)·program.backfill(지난 날 — 일별 TR 을 날마다 1회)"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "program_market_intraday",
        published="장중 연속 [확인 필요 #40: 반영 주기]",
        as_of="minute",
        store="prv_flows.program_market_intraday",
        notes=(
            "FHPPG04600101 프로그램매매 종합현황(시간)"
            "(/uapi/domestic-stock/v1/quotations/comp-program-trade-today) — 입력 거래소·시장·구간·"
            "시각. 구간·시각 입력의 뜻·응답 칸·단위 [확인 필요 #40]. 작업 program.intraday(매분)"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "program_investor_today",
        published="장중·마감 뒤 당일 누적 [확인 필요 #40]",
        as_of="trade_date",
        store="prv_flows.program_investor_daily",
        notes=(
            "HHPPG046600C1 투자자별 프로그램매매(당일)"
            "(/uapi/domestic-stock/v1/quotations/investor-program-trade-today) — 입력 시장 1 "
            "코스피·"
            "4 코스닥. 투자자 구분 코드(INVESTOR_CODES)·차익/비차익 나눔·단위 [확인 필요 #40]. "
            "**당일 TR** — 백필(지난 날)은 부르지 않고 0행(지난 날 값을 지어내지 않는다). "
            "작업 program.daily"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "program_stock_intraday",
        published="장중 체결 연속 [확인 필요 #41: 반영 주기]",
        as_of="slot10m",
        store="prv_flows.program_stock_intraday",
        notes=(
            "FHPPG04650101 종목별 프로그램매매추이(체결)"
            "(/uapi/domestic-stock/v1/quotations/program-trade-by-stock) — 입력 거래소·종목코드. "
            "프로그램 순매수 '상위' 순위 TR 은 KIS 에 없다 → 거래대금 상위 "
            "N(KIS:turnover_rank_intraday)"
            " + 관심종목 범위를 10분마다 훑는다(F2 §1.4 — 화면은 범위 꼬리표 필수). N 기본값 "
            "config/markets.yaml program.scan_top_n [확인 필요 #41]. 작업 program.stock_scan"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "program_stock_daily",
        published="장 마감 뒤 당일 확정 [확인 필요 #41: 반영 시각]",
        as_of="event",
        store="prv_flows.program_stock_daily",
        notes=(
            "FHPPG04650201 종목별 프로그램매매추이(일별)"
            "(/uapi/domestic-stock/v1/quotations/program-trade-by-stock-daily) — 입력 거래소·"
            "종목코드·날짜. 마감 뒤 전 종목 1회 훑기(코스피+코스닥 약 2,700회 — 4/s 로 약 11~12분, "
            "[확인 필요 #41]). 데이터 키 <종목>:<거래일>(event — 처리기가 claims 로 직접 선점, "
            "끊기면 이어서 — D-P4-25 ②). 작업 program.stock_sweep(18:10)"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "ws_ticks",
        published="세션 중 실시간(웹소켓)",
        as_of="minute",
        store="prv_gex.raw_messages",
        notes=(
            "KIS 웹소켓 체결·호가 — 받는 주인은 KBJ ws-gateway(서비스 gex.ws_gateway — P7, "
            "D-P7-10, ADR 0004 D2 마감). 저장 표 셋: 원문 prv_gex.raw_messages(녹화 서비스), "
            "체결 prv_gex.fut_ticks·opt_ticks(ws-gateway). 접속키는 auth 가 발급하고(kis:ws_key) "
            "ws-gateway 는 읽기만. 앱키 REST 리미터는 재연결 스냅샷만 탄다"
        ),
    ),
)
