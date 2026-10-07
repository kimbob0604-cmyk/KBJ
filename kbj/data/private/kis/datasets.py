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
"""

from __future__ import annotations

from typing import Final

from kbj.data.spec import AsOfKind, DatasetSpec, Tier, Venue

SOURCE: Final = "KIS"
LIMITER: Final = "kis"
_SPLIT: Final = (Venue.KRX, Venue.NXT, Venue.TOTAL)


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
            "quality=estimated 로 넣고 KIS:stock_investor_daily 확정치로 덮어쓴다(metrics §2). "
            "상위 몇 종목까지·정렬 옵션 [실측 필요]. 저장 표 확정은 P3 [확인 필요]"
        ),
        venues=_SPLIT,
    ),
    # ── 장중(잠정) — 순위 화면·상단 띠(P3~P5), 알림(P8) ─────────────────────────────────────────
    _kis(
        "inst_foreign_intraday",
        published="장중 가집계 — 증권사 추정치(quality=estimated), 화면에 '잠정' 표시",
        as_of="slot10m",
        store="prv_flows.stock_investor_daily",
        notes=(
            "FHPTJ04400000 장중 회차(같은 TR 이 장 마감 스냅 inst_foreign_top 과 쓰임이 달라 "
            "따로 둔다). 가집계 갱신 주기·회차 [실측 필요]. 마감 뒤 확정치로 덮어쓴다"
        ),
        venues=_SPLIT,
    ),
    _kis(
        "turnover_rank_intraday",
        published="장중 실시간 순위(잠정) — 확정은 다음 거래일 KRX 일별",
        as_of="slot10m",
        store="prv_market.stock_snapshot",
        notes=(
            "FHPST01710000 거래량 순위(/uapi/domestic-stock/v1/quotations/volume-rank). "
            "거래대금 정렬(FID_BLNG_CLS_CODE 거래금액순 추정)·상위 몇 개까지 [실측 필요]. "
            "turnover_is_estimate=true, quality=estimated. 시장 거래대금(코스피+코스닥)과 "
            "ETF·ETN 은 따로 집계(metrics §1)"
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
            "(metrics §4 — 순유입 계산은 마감 NAV 만). 표는 P5(0013) 에서 확정 [확인 필요]. "
            "ETF 의 NXT 거래 여부 [실측 필요] — 지금은 거래소를 나누지 않는다"
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
    # ── 컨센서스(P4) ──────────────────────────────────────────────────────────────────────
    _kis(
        "consensus_estimate",
        published="장 마감 뒤 [실측 필요]",
        as_of="trade_date",
        store="prv_fin.consensus_snapshot",
        notes=(
            "[추정 TR] 투자의견 FHKST668300C0·추정실적 FHKST663300C0 — 미실측(설계 R20). 실측 뒤 "
            "데이터셋을 확정한다(추정치로 채우지 않는다)"
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
        published="세션 중 연속(GX poller 주기)",
        as_of="minute",
        store="prv_gex.investor_flow",
        notes=(
            "FHPTJ04030000 시장별 투자자 시간대별"
            "(/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market) — GX poller(P7). "
            "prv_flows.market_investor_intraday 는 이 표의 뷰(설계 §8.2 0006)"
        ),
    ),
    _kis(
        "ws_ticks",
        published="세션 중 실시간(웹소켓)",
        as_of="minute",
        store="prv_gex.raw_messages",
        notes=(
            "KIS 웹소켓 체결·호가 — legacy GX ws-gateway(P7, ADR 0004 D2). 접속키는 auth 가 "
            "발급하고(kis:ws_key) ws-gateway 는 읽기만. 앱키 REST 리미터는 재연결 스냅샷만 탄다"
        ),
    ),
)
