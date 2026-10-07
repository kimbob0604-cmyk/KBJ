"""poller 설정 (docs/phase1_design.md §3·§5·§7, PLAN §4.4).

값은 모두 초기값이다 — 녹화 데이터로 조정한다(PLAN §4.4: ATM 범위·보강 2 속도는 설정으로 관리).

- 야간 동작 `night_mode`(설계 §7)는 #19 야간 실측(2026-09-29 00:42·00:44)으로 'B' 로 정했다 — 야간엔
  전광판 REST 가 주간 마감 스냅샷에서 멈추고, 단건 `EU`(옵션)·`CM`(선물)만 야간 시세를 준다.
  A = 주간과 같음(야간엔 멈춘 값), B = 전광판 없이 단건 `EU`·선물 단건 `CM`,
  C = 야간 옵션 REST 없음(웹소켓만)
- 우선순위별 대기(설계 §3 표 '모자랄 때'): P0·P1 은 기다린다(None), P2·P3 은 잠깐 기다렸다 다음
  주기로, P4 는 기다리지 않는다(0 — 토큰이 없으면 건너뛴다)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from data.kis.ratelimit import Priority

NightMode = Literal["A", "B", "C"]


class PollerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # 설계 §7 분기 B — #19 야간 실측(2026-09-29)으로 확정
    night_mode: NightMode = "B"
    # 야간 투자자별: 녹화로 값이 움직이는지 보고 결정할 때까지 수집(설계 §7)
    night_investor: bool = True

    # ── 주기 (PLAN §4.4 표) ──
    board_period_s: float = Field(default=30.0, gt=0)  # 전광판 3만기
    # 전광판 만기 사이 간격 — 리미터의 전광판 TR 최소 간격과 같게(설계 §3)
    board_gap_s: float = Field(default=1.0, ge=0)
    futures_period_s: float = Field(default=30.0, gt=0)  # 선물 전광판·기초자산
    investor_period_s: float = Field(default=60.0, gt=0)  # 투자자별 7조합
    investor_offset_s: float = Field(default=5.0, ge=0)  # 분 안에서 전광판과 덜 겹치게
    option_list_refresh_s: float = Field(default=3600.0, gt=0)  # 세션 시작 + 1시간
    option_list_retry_s: float = Field(default=60.0, gt=0)  # 실패 뒤 다시 부를 간격
    # 최종거래일 조회 실패(오류·코드 없음·빈 값) 뒤 다시 부를 간격 — 그 사이엔 캘린더 계산값
    expiry_retry_s: float = Field(default=60.0, gt=0)
    monthly_resolve: int = Field(default=2, ge=1)  # 최종거래일을 조회할 월물 수(목록 앞부터)

    # ── 보강 (설계 §5) ──
    fill1_atm_range: int = Field(default=20, ge=0)  # 월물 ATM±20 × 콜·풋 = 82건
    fill1_period_s: float = Field(default=60.0, gt=0)
    fill2_rate: float = Field(default=1.0, gt=0)  # 건/초
    # 높은 등급에 밀려 놓친 보강 2 슬롯을 이만큼까지 따라잡는다(0 이면 놓친 슬롯은 버린다)
    fill2_backlog: int = Field(default=1, ge=0)
    # 설계 §7 B: 월물 ATM±20 82건/120초
    night_b_monthly_period_s: float = Field(default=120.0, gt=0)
    # 설계 §7 B: 선물 단건 CM 2건/30초 — 근월·차월로 둔다(확인 필요)
    night_b_futures: int = Field(default=2, ge=1)

    # ── 품질 (설계 §5, PLAN §6.1) ──
    rest_stale_s: float = Field(default=90.0, gt=0)  # 전광판·보강 1: 90초 안 갱신 ok
    # 보강 1 주기가 90초보다 길면(야간 B 월물 120초) 주기 + 여유
    fill1_slack_s: float = Field(default=30.0, ge=0)
    fill2_slack_s: float = Field(default=60.0, ge=0)  # 보강 2: 순환 한 바퀴 + 여유
    # PLAN §6.1: 같은 시리즈 직전 전광판보다 행사가 개수가 이 비율을 넘게 줄면 그 스냅샷은 invalid
    board_drop_ratio: float = Field(default=0.2, gt=0, lt=1)

    # ── 호출 운영 ──
    # 리미터 설계 속도 — 우리 호출 사이를 1/4초 띄워 P4 가 자기 토큰에서 시도하게 한다
    pace_rate: float = Field(default=4.0, gt=0)
    p2_timeout_s: float = Field(default=2.0, ge=0)
    p3_timeout_s: float = Field(default=1.0, ge=0)
    fail_health_after: int = Field(default=2, ge=1)  # 같은 흐름 연속 실패 횟수 → health
    max_idle_s: float = Field(default=1.0, gt=0)  # 할 일이 없을 때 최대 대기(상태 전환 감지)

    # ── 시장구분 (#19: 전광판은 O 만, 단건은 O·EU·F·CM) ──
    board_market: str = "O"
    futures_board_market: str = "F"
    option_market: str = "O"
    night_option_market: str = "EU"
    night_futures_market: str = "CM"

    def timeout(self, priority: Priority) -> float | None:
        """레이트리미터 허가 대기 한도. None = 무기한."""
        if priority <= Priority.P1:
            return None
        if priority == Priority.P2:
            return self.p2_timeout_s
        if priority == Priority.P3:
            return self.p3_timeout_s
        return 0.0
