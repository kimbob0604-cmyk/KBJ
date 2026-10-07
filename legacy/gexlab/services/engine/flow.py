"""engine 플로우 지표 — Phase 3 항목 3 (docs/phase3_design.md §3·§7-3, metrics §6).

계산은 모두 `core.metrics.flow`(순수 함수), 여기는 engine 입력을 넘기고 입력 품질을 합성한다.

| 지표(`metrics.metric`) | 플래그 | 범위·키 | 값 | 주기 | metrics.md |
|---|---|---|---|---|---|
| `pcr_oi`·`pcr_volume` | `pcr` | all · series(시리즈 라벨) | put ÷ call | 사이클(등록부) | §6.5 |
| `max_pain` | `max_pain` | series | 행사가(pt) | 사이클(등록부) | §6.6 |
| 표 `oi_changes` | `oi_changes` | 종목(시리즈·행사가·콜풋) | OI 증감·이상치 | 사이클 | §6.7 |
| `hiro` | `hiro` | all | 딜러 헤지 수요(원) — 늘 estimated | 틱, 행은 10초 | §6.1 |
| `block_trade` | `block_trades` | series · key `라벨:행사가:콜풋:seq` | 1틱 체결량 | 틱 | §6.4 |
| `block_trades` | `block_trades` | all | 기록 거래일 수(활성 여부는 payload) | 거래일마다 | §6.4 |
| `investor_flow` | `investor_flow` | all · key `시장:업종:투자자` | 순매수(계약) | 새 행 | §6.2 |
| `dealer_check` | `dealer_check` | all | 일치 1·불일치 0(연속·경고는 payload) | POST_DAY | §6.3 |

- 입력은 그 사이클 만기 평가의 종목(`OptionQuote` — OI·당일 누적 거래량)과 마스터 상장 행사가
  (`CycleView.strikes`). F 를 쓰지 않으므로 품질은 S_ref 를 빼고 그 시리즈 체인 행 입력 품질
  (`CycleView.input_quality` — 옛 행 stale·OI 없음 estimated 등)과 합성한다. 맥스페인 동률의 F 는
  그 만기 F
- 범위 all(전체 PCR)은 평가하지 못한 시리즈(`CycleView.gaps` — 실패·최종거래일 모름)가 있으면 그
  OI 가 빠져 invalid(`series_failed`·`series_no_expiry`) — 범위 지표(`evaluate._scope_input` all)와
  같은 규칙
- 맥스페인: 상장 행사가 중 행이 한 번도 오지 않은 행사가가 있으면(세션 첫머리 — 보강 2 가 아직 돌지
  않았다) 그 OI 를 몰라 estimated(`strikes_unquoted`) [확인 필요]
- 새 지표라 기본 플래그 shadow(설계 §4) — 저장만, 발행 안 함
- OI 증감(`OiTracker`): 사이클마다 시리즈(만기 지난 시리즈는 빼고)의 종목별 쓴 체인 행
  (`evaluate.choose_rows` — 전광판 먼저)이 새 스냅샷(행 ts 가 직전보다 늦다)이면 `oi_step`.
  행의 ts = 스냅샷 시각(체인 행 수신 시각 — 히트맵의 시각 축, 이상치로 고칠 때 같은 키), prev_ts =
  직전 스냅샷. 기본값 [확인 필요]: 세션(귀속 거래일·세션)이 바뀌면 비운다(첫 스냅샷 증감 없음 —
  주간·야간 OI 를 잇지 않는다, 재기동도 같다), 행은 첫 스냅샷·증감이 0 이 아닌 스냅샷·이상치로
  바뀐 칸만 쓴다(0 증감도 '다음 스냅샷'으로 이상치 판정에는 든다), OI 없는 행은 스냅샷이 아니다.
  첫 스냅샷 칸(증감 없음)은 저장된 같은 키의 칸을 덮지 않는다(`OI_CHANGES.keep_if_null` — 세션
  중간 재기동이 같은 최신 행을 첫 스냅샷으로 다시 써도 증감·이상치가 남는다).
  이상치가 나면 두 칸(앞 칸은 같은 키로 고쳐 쓴다)과 health `engine_oi_outlier`(시리즈마다 10분에
  한 번). 품질 = 이번·직전 두 스냅샷 체인 행 품질 중 나쁜 것(증감이 기대는 입력 — 첫 스냅샷은 그
  행 품질, 이상치로 고친 앞 칸은 그 칸 품질)
- HIRO-lite·대량 체결(`TickFlow`): `ticks.opt`(옵션 체결 — ws-gateway `opt_ticks` 행)마다
  `hiro_step`. Δ·F 는 마지막 사이클의 그 종목 자체 델타·그 만기 F(`learn` — `option_iv` 행; KIS
  델타는 쓰지 않는다). 리셋: 시퀀스 공백(`ticks.fut`·`ticks.opt` 공통 수신 순번 — 둘 다 받는다),
  웹소켓 끊김(서비스가 ws-gateway 연결 사건을 보고 `disconnected` — ws-gateway 가 재연결마다 순번
  하나를 건너뛰므로 보통은 재연결 뒤 첫 틱의 순번 공백이 먼저 리셋하고, 그 사건 뒤에 이미 리셋했으면
  다시 비우지 않고 사유만 `ws_disconnect` 로), 세션 전환(틱의 귀속 거래일·세션). 역행
  틱은 버리고 health `engine_hiro_reversal`(종목마다 10분에 한 번). 리셋 자체는 health 가 아니다 —
  hiro 행 payload 의 `reset_reason`·`reset_at`(표시)과 로그. 행은 바뀐 것이 있을 때(`rows` — ts =
  마지막 틱 또는 리셋 시각), 리셋·세션 전환 직전 상태도 한 행(행 주기 사이의 흐름을 잃지 않게).
  기본값 [확인 필요]: Δ·F 는 그 사이클과 같은 세션(귀속 거래일·세션)의 틱에만 — 세션 첫 사이클
  전 틱은 Δ 모름(`unpriced_qty`, 야간 첫머리에 주간 델타를 쓰지 않는다), 시리즈를 모르는 틱(마스터
  밖 — 코스피200 옵션이 아니다)은 순번만 보고 건너뛴다, 시퀀스 공백 리셋 시각은 그 틱 수신 시각
- 대량 체결: 거래일마다 `block_thresholds`(서비스가 opt_ticks 20거래일 표본을 읽는다 — 그 전엔
  비활성), 옵션 틱의 1틱 체결량이 그 머니니스 구간(`K/F − 1`, F 는 마지막 사이클 F) p99 이상이면
  `block_trade` 행. 기본값 [확인 필요]: 품질 ok(구간은 마지막 사이클 F — 틱 시점 F 가 아니다),
  F 를 모르는 틱은 판정하지 않는다, 표본의 F 는 그 틱 앞 10분 안의 같은 종목 engine F(`option_iv`)
- 투자자별 순매수(`InvestorFeed`, §6.2): `investor_flow`(poller 60초, 7조합 × 12투자자) 중 외국인·
  개인·기관계·증권(딜러 프록시) 새 행을 그대로 `investor_flow` 지표 행으로(ts = 그 행 시각, 품질 =
  그 행 품질). 기본값 [확인 필요]: 30초마다 지금 세션의 조합·투자자별 최신 행을 보고 직전에 낸
  것보다 늦은 행만, 순매수 수량이 없으면 null·invalid(`field_missing`)
- 딜러 가정 점검(`dealer_record`, §6.3): 거래일마다 `POST_DAY` 한 번 — 그날 주간 조합별 마지막
  증권(scrt) 행의 콜(K2I OC01·WKM OC05·WKI OC04)·풋(OP01·OP05·OP04) 순매수 수량 합. 행 ts = 그날
  15:45 KST. 연속 불일치는 앞 거래일 이 지표 행에서 센다. 기본값 [확인 필요]: 주간만(야간 투자자별은
  미실측 #13), 앞 20거래일까지 보고 행이 없는 날은 연속을 끊는다, 품질에 쓴 행 품질을 합성
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, cast, get_args

from core.metrics.flow import (
    DEALER_WARN_DAYS,
    INACTIVE,
    OI_RECOVERY,
    BlockThresholds,
    HiroEvent,
    HiroState,
    HiroTick,
    OiCell,
    OiSnapshot,
    Pcr,
    Ratio,
    ResetReason,
    dealer_check,
    dealer_warning,
    hiro_reset,
    hiro_step,
    is_block,
    is_seq_gap,
    max_pain,
    mismatch_streak,
    moneyness_bucket,
    oi_step,
    pcr,
)
from core.preprocess import Quality, worst
from data.store import FuturesTickRecord, OptionTickRecord
from services.bus import MrktCls, SeriesKey, series_label
from services.engine.evaluate import CycleInput, CycleResult, EngineHealth, choose_rows
from services.engine.records import MetricRecord, OiChangeRecord
from services.engine.registry import CycleView, Flag, MetricPlugin, PluginValue
from services.poller.records import ChainRecord, InvestorRecord

OI_FLAG = "oi_changes"  # 기능 플래그 이름 — oi_changes 표
HIRO = "hiro"  # §6.1 지표·플래그 이름
BLOCK_TRADE = "block_trade"  # §6.4 체결 한 건
BLOCK_STATUS = "block_trades"  # §6.4 그 거래일 기준(활성 여부) — 두 지표의 플래그 이름
HIRO_EVERY_S = 10.0  # hiro 행을 쓰는 주기(바뀐 것이 있을 때만) [확인 필요]
# 대량 체결 표본의 F — 그 틱 앞 이만큼 안의 같은 종목 engine F(option_iv) [확인 필요]
BLOCK_F_LOOKBACK = timedelta(minutes=10)
_CLASSES = frozenset(get_args(MrktCls))

# ── PCR·맥스페인 (§6.5·§6.6 — 사이클 등록부) ──────────────────────────────────


def _pcr(view: CycleView, pick: Callable[[Pcr], Ratio]) -> list[PluginValue]:
    """시리즈마다 PCR 과 전체(all) — 전체는 평가한 시리즈 종목을 모두 모은 합의 비율."""
    out: list[PluginValue] = []
    legs: list[tuple[str, int | None, int | None]] = []
    qs: list[Quality] = []
    reasons: list[str] = []
    for e in view.evals:
        mine = [(o.quote.cp, o.quote.oi, o.quote.volume) for o in e.options]
        r = pick(pcr(mine))
        in_q = view.input_quality.get(e.expiry, "ok")
        in_r = view.input_reasons.get(e.expiry, ())
        payload: dict[str, Any] = {
            "put": r.put,
            "call": r.call,
            "missing": r.missing,
            "reasons": [*r.reasons, *in_r],
        }
        out.append(PluginValue("series", r.value, worst(r.quality, in_q), e.expiry, payload))
        legs += mine
        qs.append(in_q)
        reasons += [f"{e.expiry}:{x}" for x in in_r]
    for g in view.gaps:  # 평가하지 못한 시리즈의 OI 가 전체에서 빠졌다
        qs.append("invalid")
        why = "series_failed" if g.status == "failed" else "series_no_expiry"
        if why not in reasons:
            reasons.append(why)
    total = pick(pcr(legs))
    payload = {
        "put": total.put,
        "call": total.call,
        "missing": total.missing,
        "expiries": [e.expiry for e in view.evals],
        "reasons": [*total.reasons, *reasons],
    }
    out.append(PluginValue("all", total.value, worst(total.quality, *qs), "", payload))
    return out


def compute_pcr_oi(view: CycleView) -> list[PluginValue]:
    """§6.5 OI 기준 PCR — 시리즈마다·전체."""
    return _pcr(view, lambda p: p.oi)


def compute_pcr_volume(view: CycleView) -> list[PluginValue]:
    """§6.5 거래량 기준 PCR(당일 누적 거래량) — 시리즈마다·전체."""
    return _pcr(view, lambda p: p.volume)


def compute_max_pain(view: CycleView) -> list[PluginValue]:
    """§6.6 시리즈마다 맥스페인 — 후보는 마스터 상장 행사가(없으면 행이 온 행사가)."""
    out: list[PluginValue] = []
    for e in view.evals:
        listed = view.strikes.get(e.expiry)
        mp = max_pain(((o.quote.strike, o.quote.cp, o.quote.oi) for o in e.options), listed, e.F)
        quoted = {o.quote.strike for o in e.options}
        unquoted = [k for k in listed or () if k not in quoted]
        reasons = list(mp.reasons)
        q = worst(mp.quality, view.input_quality.get(e.expiry, "ok"))
        if unquoted:
            q = worst(q, "estimated")
            reasons.append("strikes_unquoted")
        payload: dict[str, Any] = {
            "pain_won": mp.pain_won,
            "tied": mp.tied,
            "candidates": mp.candidates,
            "unquoted": len(unquoted),
            "forward": e.F,
            "listed": listed is not None,
            "reasons": [*reasons, *view.input_reasons.get(e.expiry, ())],
        }
        value = None if mp.strike is None else float(mp.strike)
        out.append(PluginValue("series", value, q, e.expiry, payload))
    return out


FLOW_REGISTRY: tuple[MetricPlugin, ...] = (
    MetricPlugin("pcr_oi", "pcr", compute_pcr_oi),
    MetricPlugin("pcr_volume", "pcr", compute_pcr_volume),
    MetricPlugin("max_pain", "max_pain", compute_max_pain),
)


# ── OI 증감 (§6.7 — oi_changes 표) ─────────────────────────────────────────────

OiKey = tuple[str, str, Decimal, str]  # (시장분류, 만기, 행사가, 콜풋)


class OiTracker:
    """종목마다 직전 스냅샷의 칸을 들고 사이클마다 새 스냅샷을 `oi_step` 으로 잇는다."""

    def __init__(self, recovery: Decimal = OI_RECOVERY) -> None:
        self.recovery = recovery
        self.tag: tuple[date, str] | None = None
        self.flag: Flag = "shadow"  # 지금 사이클의 기능 플래그(`cycle`)
        # 종목 → (직전 스냅샷의 칸, 그 체인 행 품질, 그 칸 품질)
        self.last: dict[OiKey, tuple[OiCell, Quality, Quality]] = {}

    def cycle(
        self, inp: CycleInput, result: CycleResult, flag: Flag = "shadow"
    ) -> tuple[list[OiChangeRecord], list[EngineHealth]]:
        """이번 사이클의 새 스냅샷 → 쓸 행(새 칸·이상치로 바뀐 앞 칸)과 health. flag: 계산 당시
        기능 플래그(행의 `flag` — 이상치로 고친 앞 칸도 지금 플래그)."""
        self.flag = flag
        tag = (inp.trade_date, inp.session)
        if tag != self.tag:
            self.tag = tag
            self.last.clear()
        live = {o.key for o in result.series if o.status != "expired"}
        groups: dict[SeriesKey, list[ChainRecord]] = {}
        for r in inp.chain:
            if (r.trade_date, r.session) != tag or r.mrkt_cls not in _CLASSES:
                continue
            key = cast(SeriesKey, (r.mrkt_cls, r.expiry))
            if key in live:
                groups.setdefault(key, []).append(r)
        out: list[OiChangeRecord] = []
        health: list[EngineHealth] = []
        for key in sorted(groups):
            for (strike, cp), row in sorted(choose_rows(groups[key]).items()):
                self._one(key, strike, cp, row, out, health)
        return out, health

    def _one(
        self,
        key: SeriesKey,
        strike: Decimal,
        cp: str,
        row: ChainRecord,
        out: list[OiChangeRecord],
        health: list[EngineHealth],
    ) -> None:
        if row.oi is None:
            return
        k: OiKey = (key[0], key[1], strike, cp)
        held = self.last.get(k)
        if held is not None and row.ts <= held[0].ts:
            return  # 같은 스냅샷(새 행이 오지 않았다)
        cell, prev = oi_step(
            None if held is None else held[0], OiSnapshot(row.ts, row.oi), recovery=self.recovery
        )
        # 증감은 이번·직전 두 스냅샷에만 기댄다 — 칸 품질도 그 두 체인 행 품질만(앞 칸 품질을
        # 이으면 세션 앞쪽 한 스냅샷의 품질이 뒤 칸 모두에 번진다)
        quality = row.quality if held is None else worst(row.quality, held[1])
        self.last[k] = (cell, row.quality, quality)
        if prev is not None and held is not None:
            out.append(self._record(k, prev, held[2]))  # 이상치로 고친 앞 칸 — 그 칸 품질
            label = series_label(key)
            dip = prev.change or 0
            health.append(
                EngineHealth(
                    "engine_oi_outlier",
                    "warning",
                    f"{label} {strike} {cp}: OI {prev.prev_oi}→{prev.oi}→{cell.oi} — 줄어든 "
                    f"{-dip} 의 {cell.change} 복구, 두 칸 격리(표시 제외)",
                    subject=label,
                )
            )
        if cell.change is None or cell.change != 0 or cell.outlier:
            out.append(self._record(k, cell, quality))

    def _record(self, k: OiKey, cell: OiCell, quality: Quality) -> OiChangeRecord:
        if self.tag is None:  # cycle() 이 먼저 세운다 — 방어선
            raise RuntimeError("세션 없이 OI 행을 만들 수 없다")
        cls, expiry, strike, cp = k
        return OiChangeRecord(
            ts=cell.ts,
            trade_date=self.tag[0],
            session=cast(Any, self.tag[1]),
            mrkt_cls=cls,
            expiry=expiry,
            strike=strike,
            cp=cast(Any, cp),
            oi=cell.oi,
            prev_ts=cell.prev_ts,
            prev_oi=cell.prev_oi,
            change=cell.change,
            outlier=cell.outlier,
            quality=quality,
            flag=self.flag,
        )


# ── HIRO-lite·대량 체결 (§6.1·§6.4 — ticks.opt·ticks.fut) ──────────────────────

GreekKey = tuple[str, str, Decimal, str]  # (시장분류, 만기, 행사가, 콜풋)
# 연결 사건에 한 일 — 리셋·사유만 바꿈(이미 그 사건 뒤에 리셋했다)
DisconnectAction = Literal["reset", "relabel"] | None


class TickFlow:
    """틱 스트림 상태 — HIRO-lite 누적, 수신 순번, 마지막 사이클의 자체 델타·F, 대량 체결 기준."""

    def __init__(self) -> None:
        self.hiro = HiroState()
        self.last_seq: int | None = None
        self.greeks: dict[GreekKey, tuple[float | None, float | None]] = {}
        self.greeks_tag: tuple[date, str] | None = None  # 델타·F 를 배운 사이클의 세션
        self.thresholds: BlockThresholds = INACTIVE
        self.thresholds_for: date | None = None  # 기준을 읽은 거래일
        self.events: Counter[str] = Counter()  # hiro_step 결과별 틱 수(+ 시리즈 없는 틱)
        self.blocks: list[tuple[OptionTickRecord, int, int, float]] = []  # (틱, 구간, 기준, F)
        self.health: list[EngineHealth] = []
        self.pending: list[HiroState] = []  # 리셋·세션 전환 직전 상태(아직 행으로 내지 않은)
        self.dirty = False  # 마지막 rows() 뒤 지금 상태가 바뀌었나

    # ── 입력 ──

    def learn(self, result: CycleResult) -> None:
        """마지막 사이클의 종목별 자체 델타·그 만기 F(`option_iv` 행 — GEX 에 든 종목만 델타가
        있다)로 바꾼다. 그 사이클 세션의 틱에만 쓴다(다른 세션 틱은 Δ 모름)."""
        self.greeks = {
            (r.mrkt_cls, r.expiry, r.strike, r.cp): (r.delta, r.forward) for r in result.option_iv
        }
        self.greeks_tag = (result.trade_date, result.session)

    def _keep(self) -> None:
        """리셋·세션 전환 직전 상태를 행으로 남길 목록에 — 마지막 rows() 뒤 바뀐 것이 있을 때만."""
        if self.dirty and self.hiro.trade_date is not None:
            self.pending.append(self.hiro)
        self.dirty = False

    def reset(self, reason: ResetReason, at: datetime) -> bool:
        """누적을 비운다 — 아직 틱이 없으면(세션 없음) 아무것도 하지 않는다."""
        if self.hiro.trade_date is None:
            return False
        self._keep()
        self.hiro = hiro_reset(self.hiro, reason, at)
        self.dirty = True
        return True

    def disconnected(self, last_event: datetime, at: datetime) -> DisconnectAction:
        """ws-gateway 연결 사건(끊김·재연결·연결 실패 — 가장 늦은 것이 last_event)을 봤다 → 리셋
        (`ws_disconnect`, 시각 at). 단 그 사건 뒤에 이미 순번 공백·끊김으로 리셋했으면(ws-gateway
        가 재연결마다 순번 하나를 건너뛰어 재연결 뒤 첫 틱이 먼저 리셋한다) 누적은 그 사건 뒤의
        것뿐이라 다시 비우지 않고 사유만 `ws_disconnect` 로. 무엇을 했나(할 일이 없으면 None)."""
        st = self.hiro
        at_or_after = st.reset_at is not None and st.reset_at >= last_event
        if not (at_or_after and st.reset_reason in ("seq_gap", "ws_disconnect")):
            return "reset" if self.reset("ws_disconnect", at) else None
        if st.reset_reason == "ws_disconnect":
            return None
        self.hiro = replace(st, reset_reason="ws_disconnect")
        self.dirty = True
        return "relabel"

    def on_seq(self, seq: int, at: datetime) -> bool:
        """수신 순번 — 이어지지 않으면 리셋(`seq_gap`, 시각 at). 공백이었나."""
        gap = is_seq_gap(self.last_seq, seq)
        self.last_seq = seq
        if gap:
            self.reset("seq_gap", at)
        return gap

    def on_futures(self, rec: FuturesTickRecord) -> None:
        """선물 틱은 수신 순번만(옵션 틱과 같은 순번을 쓴다)."""
        self.on_seq(rec.seq, rec.received_at)

    def stop_hiro(self) -> None:
        """HIRO 플래그 off — 누적과 아직 행으로 내지 않은 상태를 버린다. 다시 켜면 처음부터
        (`start` — 재기동과 같다: 종목의 첫 틱은 기준만이라 끈 동안의 흐름이 들지 않는다). 순번은
        그대로 본다."""
        self.hiro = HiroState()
        self.pending = []
        self.dirty = False

    def on_option(
        self, rec: OptionTickRecord, *, hiro: bool = True, blocks: bool = True
    ) -> HiroEvent | None:
        """옵션 틱 하나 — hiro 면 HIRO-lite, blocks 면 대량 체결 판정. hiro 가 꺼졌으면 누적·역행
        health 없이 순번(과 대량 체결)만 보고 None(`stop_hiro`). 시리즈를 모르는 틱(마스터 밖 —
        코스피200 옵션이 아니다)은 순번만 보고 None."""
        if not hiro:
            self.stop_hiro()
        st = self.hiro
        if st.trade_date is not None and (rec.trade_date, rec.session) != (
            st.trade_date,
            st.session,
        ):
            self._keep()  # 세션 전환 — 앞 세션 마지막 상태를 남긴다(리셋은 hiro_step)
        # 리셋 시각은 수신 시각 — 초 단위 체결 시각과 겹쳐 앞 상태 행을 덮지 않게
        self.on_seq(rec.seq, rec.received_at)
        key = self._key(rec)
        if key is None:
            self.events["no_series"] += 1
            return None
        same = self.greeks_tag == (rec.trade_date, rec.session)
        delta, forward = self.greeks.get(key, (None, None)) if same else (None, None)
        if not hiro:
            if blocks:
                self._block(rec, key, forward)
            return None
        tick = HiroTick(
            rec.code,
            rec.trade_date,
            rec.session,
            rec.ts,
            rec.cum_buy_qty,
            rec.cum_sell_qty,
            delta,
            forward,
        )
        self.hiro, ev = hiro_step(self.hiro, tick)
        self.events[ev] += 1
        self.dirty = True
        if ev == "reversal":
            self.health.append(
                EngineHealth(
                    "engine_hiro_reversal",
                    "warning",
                    f"{rec.code}: 누적 매수·매도 체결수량이 줄었다({rec.cum_buy_qty}·"
                    f"{rec.cum_sell_qty}) — 그 틱은 버린다",
                    subject=rec.code,
                )
            )
        if blocks:
            self._block(rec, key, forward)
        return ev

    @staticmethod
    def _key(rec: OptionTickRecord) -> GreekKey | None:
        cls = rec.mrkt_cls
        if cls is None or cls not in _CLASSES or rec.expiry is None:
            return None
        if rec.strike is None or rec.cp is None:
            return None
        return (cls, rec.expiry, rec.strike, rec.cp)

    def _block(self, rec: OptionTickRecord, key: GreekKey, forward: float | None) -> None:
        th = self.thresholds
        if not th.active or forward is None or rec.qty is None:
            return
        _, _, strike, cp = key
        bucket = moneyness_bucket(strike, forward)
        if is_block(th, cp, bucket, rec.qty):
            self.blocks.append((rec, bucket, th.table[(cast(Any, cp), bucket)], forward))

    # ── 출력 ──

    def drain_health(self) -> list[EngineHealth]:
        out, self.health = self.health, []
        return out

    def rows(self, flag: Flag) -> list[MetricRecord]:
        """바뀐 것이 있으면 hiro 행 — 리셋·세션 전환 직전 상태들과 지금 상태(ts = 마지막 틱 또는
        리셋 시각). 없으면 빈 목록."""
        states = self.pending
        if self.dirty and self.hiro.trade_date is not None:
            states = [*states, self.hiro]
        self.pending, self.dirty = [], False
        return [hiro_record(s, flag) for s in states]

    def drain_blocks(self, flag: Flag) -> list[MetricRecord]:
        """판정된 대량 체결 → `block_trade` 행(key 는 시리즈 라벨·행사가·콜풋·수신 순번)."""
        out: list[MetricRecord] = []
        days = self.thresholds.days
        window = (days[0], days[-1]) if days else ()
        for rec, bucket, limit, forward in self.blocks:
            label = series_label(cast(SeriesKey, (rec.mrkt_cls, rec.expiry)))
            payload: dict[str, Any] = {
                "code": rec.code,
                "strike": float(rec.strike or 0),
                "cp": rec.cp,
                "price": float(rec.price),
                "bucket": bucket,
                "threshold": limit,
                "forward": forward,
                "days": [d.isoformat() for d in window],
            }
            out.append(
                MetricRecord(
                    ts=rec.ts,
                    trade_date=rec.trade_date,
                    session=rec.session,
                    metric=BLOCK_TRADE,
                    scope="series",
                    key=f"{label}:{rec.strike}:{rec.cp}:{rec.seq}",
                    value=float(rec.qty or 0),
                    payload=payload,
                    quality="ok",
                    flag=flag,
                )
            )
        self.blocks = []
        return out


def hiro_record(st: HiroState, flag: Flag) -> MetricRecord:
    """HIRO-lite 상태 하나 → `hiro` 행(값 = 딜러 헤지 수요 원, 늘 estimated)."""
    if st.trade_date is None or st.session is None:
        raise ValueError("세션 없는 HIRO 상태는 행이 아니다")
    stamps = [t for t in (st.last_ts, st.reset_at) if t is not None]
    if not stamps:
        raise ValueError("시각 없는 HIRO 상태는 행이 아니다")
    payload: dict[str, Any] = {
        "customer_delta_flow": st.customer_flow,
        "signed_qty": st.signed_qty,
        "ticks": st.ticks,
        "unpriced_qty": st.unpriced_qty,
        "reversals": st.reversals,
        "codes": len(st.cums),
        "reset_reason": st.reset_reason,
        "reset_at": None if st.reset_at is None else st.reset_at.isoformat(),
        "last_tick_ts": None if st.last_ts is None else st.last_ts.isoformat(),
    }
    return MetricRecord(
        ts=max(stamps),
        trade_date=st.trade_date,
        session=cast(Any, st.session),
        metric=HIRO,
        scope="all",
        value=st.dealer_hedge,
        payload=payload,
        quality=st.quality,
        flag=flag,
    )


def block_status(
    th: BlockThresholds, at: datetime, trade_date: date, session: str, flag: Flag, need: int
) -> MetricRecord:
    """그 거래일 대량 체결 기준 — 값 = 기록 거래일 수, payload 에 활성 여부(need 미만 비활성)."""
    payload: dict[str, Any] = {
        "active": th.active,
        "need_days": need,
        "first_day": th.days[0].isoformat() if th.days else None,
        "last_day": th.days[-1].isoformat() if th.days else None,
        "buckets": len(th.table),
        "samples": th.samples,
    }
    return MetricRecord(
        ts=at,
        trade_date=trade_date,
        session=cast(Any, session),
        metric=BLOCK_STATUS,
        scope="all",
        value=float(len(th.days)),
        payload=payload,
        quality="ok",
        flag=flag,
    )


# ── 투자자별 선물·옵션 순매수 (§6.2 — investor_flow 시계열 그대로) ──────────────────

INVESTOR_FLOW = "investor_flow"  # 지표·플래그 이름
INVESTOR_EVERY_S = 30.0  # investor_flow 새 행을 보는 주기(poller 60초) [확인 필요]
# §6.2 표시 투자자 — 외국인·개인·기관계·증권(딜러 프록시)
SHOWN_INVESTORS: tuple[str, ...] = ("frgn", "prsn", "orgn", "scrt")
DEALER_PROXY = "scrt"
# poller 조합(시장, 업종) → (상품, 시리즈) — probe_results #13, services/poller/endpoints.py
INVESTOR_PAIRS: dict[tuple[str, str], tuple[str, str]] = {
    ("K2I", "F001"): ("futures", "K2I"),
    ("K2I", "OC01"): ("call", "K2I"),
    ("K2I", "OP01"): ("put", "K2I"),
    ("WKM", "OC05"): ("call", "WKM"),
    ("WKM", "OP05"): ("put", "WKM"),
    ("WKI", "OC04"): ("call", "WKI"),
    ("WKI", "OP04"): ("put", "WKI"),
}
InvestorKey = tuple[str, str, str]  # (시장, 업종, 투자자)


class InvestorFeed:
    """investor_flow 의 새 행(투자자·조합마다 직전에 낸 것보다 늦은 ts)을 `investor_flow` 지표
    행으로 그대로 옮긴다. 세션(귀속 거래일·세션)이 바뀌면 비운다."""

    def __init__(self) -> None:
        self.tag: tuple[date, str] | None = None
        self.last: dict[InvestorKey, datetime] = {}

    def rows(
        self, tag: tuple[date, str], records: Iterable[InvestorRecord], flag: Flag
    ) -> list[MetricRecord]:
        if tag != self.tag:
            self.tag, self.last = tag, {}
        out: list[MetricRecord] = []
        for r in sorted(records, key=lambda r: r.ts):
            pair = INVESTOR_PAIRS.get((r.market_code, r.sector_code))
            if pair is None or r.investor not in SHOWN_INVESTORS:
                continue
            if (r.trade_date, r.session) != tag:
                continue
            k: InvestorKey = (r.market_code, r.sector_code, r.investor)
            seen = self.last.get(k)
            if seen is not None and r.ts <= seen:
                continue
            self.last[k] = r.ts
            out.append(investor_record(r, pair, flag))
        return out


def investor_record(r: InvestorRecord, pair: tuple[str, str], flag: Flag) -> MetricRecord:
    """investor_flow 한 행 → 지표 행(값 = 순매수 수량 계약, payload 에 대금·매수·매도). 순매수
    수량이 없으면 null·invalid(`field_missing`) [확인 필요]. 합계 데이터(행사가별 아님), 증권은
    딜러 프록시."""
    product, series = pair
    quality: Quality = r.quality
    reasons: list[str] = []
    if r.net_qty is None:
        quality = worst(quality, "invalid")
        reasons.append("field_missing")
    payload: dict[str, Any] = {
        "market_code": r.market_code,
        "sector_code": r.sector_code,
        "investor": r.investor,
        "product": product,
        "series": series,
        "net_qty": r.net_qty,
        "net_value": r.net_value,
        "buy_qty": r.buy_qty,
        "sell_qty": r.sell_qty,
        "buy_value": r.buy_value,
        "sell_value": r.sell_value,
        "units": {"qty": "contracts", "value": "million_krw"},
        "aggregate": True,  # 합계 데이터 — 행사가별이 아니다
        "dealer_proxy": r.investor == DEALER_PROXY,
        "reasons": reasons,
    }
    return MetricRecord(
        ts=r.ts,
        trade_date=r.trade_date,
        session=r.session,
        metric=INVESTOR_FLOW,
        scope="all",
        key=f"{r.market_code}:{r.sector_code}:{r.investor}",
        value=None if r.net_qty is None else float(r.net_qty),
        payload=payload,
        quality=quality,
        flag=flag,
    )


# ── 딜러 가정 점검 (§6.3 — 거래일마다 POST_DAY 한 번) ─────────────────────────────

DEALER_CHECK = "dealer_check"  # 지표·플래그 이름
DEALER_LOOKBACK_DAYS = 20  # 연속 불일치를 세는 앞 거래일 수 [확인 필요]
# 증권 계정 콜·풋 순매수를 더하는 조합 — 월물·위클리(월·목) (§6.3 "월물·위클리 합산")
CALL_PAIRS: tuple[tuple[str, str], ...] = (("K2I", "OC01"), ("WKM", "OC05"), ("WKI", "OC04"))
PUT_PAIRS: tuple[tuple[str, str], ...] = (("K2I", "OP01"), ("WKM", "OP05"), ("WKI", "OP04"))


def dealer_record(
    trade_date: date,
    at: datetime,
    records: Iterable[InvestorRecord],
    history: Iterable[MetricRecord],
    days: Sequence[date],
    flag: Flag,
    *,
    warn_days: int = DEALER_WARN_DAYS,
) -> MetricRecord:
    """§6.3 그 거래일 한 행 — 값 = 일치 1·불일치 0(판정 없으면 null), payload 에 콜·풋 순매수 합·
    조합별 값·연속 불일치 거래일 수·경고(연속 warn_days 이상 — 대시보드 배지).

    records: 그날 주간 조합·투자자별 마지막 investor_flow 행. history: 앞 거래일의 이 지표 행.
    days: 연속을 셀 앞 거래일(오름차순) — 행이 없거나 판정 없는 날은 연속을 끊는다."""
    scrt = {
        (r.market_code, r.sector_code): r
        for r in records
        if r.investor == DEALER_PROXY and (r.trade_date, r.session) == (trade_date, "day")
    }
    calls = [scrt[p].net_qty if p in scrt else None for p in CALL_PAIRS]
    puts = [scrt[p].net_qty if p in scrt else None for p in PUT_PAIRS]
    check = dealer_check(calls, puts)
    used: list[Quality] = [row.quality for row in scrt.values()]
    quality = worst(check.quality, *used)
    past: dict[date, bool | None] = {}
    for h in sorted(history, key=lambda h: (h.trade_date, h.ts)):
        v = h.payload.get("consistent")
        past[h.trade_date] = v if isinstance(v, bool) else None
    streak = mismatch_streak([*(past.get(d) for d in days), check.consistent])
    payload: dict[str, Any] = {
        "call_net": check.call_net,
        "put_net": check.put_net,
        "consistent": check.consistent,
        "mismatch_streak": streak,
        "warning": dealer_warning(streak, warn_days),
        "warn_days": warn_days,
        "lookback_days": len(days),
        "pairs": {
            f"{m}:{s}": (scrt[(m, s)].net_qty if (m, s) in scrt else None)
            for m, s in (*CALL_PAIRS, *PUT_PAIRS)
        },
        "investor": DEALER_PROXY,
        "basis": "flow",  # 플로우(거래)이지 포지션(OI)이 아니다
        "reasons": list(check.reasons),
    }
    return MetricRecord(
        ts=at,
        trade_date=trade_date,
        session="day",
        metric=DEALER_CHECK,
        scope="all",
        value=None if check.consistent is None else float(check.consistent),
        payload=payload,
        quality=quality,
        flag=flag,
    )
