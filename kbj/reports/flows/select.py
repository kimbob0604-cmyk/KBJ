"""수급 리포트 대상 고르기 — 보드 신고가(`prv_board.artifact` newhigh) ∧ 거래량 배수.

승격 원본: ET `monitor/flow/run.py` 의 `pick_targets`·`basis_of`·`hit`·`meta_of`·`TOP_N`·
`VOL_MULT_MIN`·`KIND`·`KINDS`(본문 그대로 — docs/p5_design.md §1.5·§4.5).

KBJ P5 에서 바뀐 것
- 입력은 ET `board/state/<날짜>/newhigh.json` 대신 kbj 보드 엔진이 저장한 같은 키의 산출
  (`BoardRepo.artifact(asof, "newhigh")` — `achieved`·`as_of`)이다. 읽기는 `latest_newhigh`
  하나(`run.py` 가 부른다). 다른 묶음의 코드를 import 하지 않는다(계약 ⑯ — 저장된 행만).
- 신고가 3축(ADR 0017): 60일(d60)이 120일(d120)로 바뀌었다. 기본 대상은 그대로 **52주(w52)** —
  역사적 신고가도 52주에 든다(보드 알약과 같은 `hits[k]` 규약).

판정 기준은 **종가**다(`hit()`) — 고가 기준은 장중에 잠깐 뚫고 종가는 밀린 날도 신고가로 잡는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

__all__ = [
    "KIND",
    "KINDS",
    "LABEL_KO",
    "TOP_N",
    "VOL_MULT_MIN",
    "basis_of",
    "hit",
    "latest_newhigh",
    "meta_of",
    "pick_targets",
]

# 기본 대상 종목 수. 한 종목당 그림 3~4장이라 너무 늘리면 대화창이 잠긴다.
# 52주 이상 신고가는 보통 하루 한 자릿수라 이 수에 먼저 닿는 일이 드물다.
TOP_N = 12

# 거래량 문턱 — 그날 거래량이 20거래일 평균의 몇 배 이상이어야 대상인가.
#
# **기본값은 0(문턱 없음)이다.** 전에는 2.0 이었는데, 그러면 52주 신고가를 낸
# 종목의 수급이 거래량 때문에 통째로 빠진다 — 실제로 2026-09-17 에 6종목 중
# 2종목만 남았고, 나머지 넷은 신고가를 냈는데도 수급이 한 줄도 안 갔다.
# 신고가를 낸 종목의 수급은 빠짐없이 본다는 것이 이 리포트의 일이다.
#
# 보드가 이미 매 종목에 `vol_mult`(당일 거래량 ÷ 20일 평균)를 실어 보내므로 그 값을
# 그대로 쓴다 — 여기서 다시 계산하면 화면의 '거래량 N배' 와 대상이 갈린다.
VOL_MULT_MIN = 0.0

# 어느 신고가를 볼 것인가. 보드의 라벨 키다 — hist(역사적) · w52(52주) · d120(120일 — ADR 0017).
KIND = "w52"
KINDS = ("hist", "w52", "d120", "all")

LABEL_KO = {"hist": "역사적", "w52": "52주", "d120": "120일"}


class _BoardReader(Protocol):
    def artifact(self, asof: date, name: str) -> Any: ...

    def last_day(self, upto: date) -> date | None: ...


def latest_newhigh(board: _BoardReader, upto: date) -> dict[str, Any] | None:
    """`upto` 이하 가장 최근 보드의 newhigh 산출(payload). 없으면 None.

    ET 는 `state/*/newhigh.json` 의 마지막 파일을 읽었다. 같은 뜻으로 '그날 이하 마지막 보드'를
    읽는다 — 보드가 아직 안 돌았으면 전 거래일 보드이고, 본문이 그 차이를 말한다(`asof_gap`).
    """
    day = board.last_day(upto)
    if day is None:
        return None
    art = board.artifact(day, "newhigh")
    if art is None:
        return None
    return dict(art.payload)


def _hits(row: dict[str, Any], key: str, kind: str) -> bool:
    return bool(((row.get(key) or {}).get("hits") or {}).get(kind))


def basis_of(row: dict[str, Any], kind: str) -> str | None:
    """그 신고가가 **어느 기준으로** 섰는가. '고가' · '종가' · '고가·종가' · None.

    기준은 이름으로 읽는다(`high_basis` · `close_basis`). `hits` 는 보드의 기본
    기준이라 설정(`default_basis`)에 따라 가리키는 기준이 바뀌는데, 그걸 '고가'
    로 짐작하면 설정을 바꾸는 순간 리포트가 틀린 기준을 적는다.

    `high_basis` 가 없는 옛 산출은 `hits` 로 물러서되, 대상 선정은 어차피 종가 기준이라
    잘못 뽑힐 일은 없다.
    """
    cl = _hits(row, "close_basis", kind)
    hi = (
        _hits(row, "high_basis", kind)
        if row.get("high_basis") is not None
        else bool((row.get("hits") or {}).get(kind))
    )
    if hi and cl:
        return "고가·종가"
    return "고가" if hi else ("종가" if cl else None)


def hit(row: dict[str, Any], kind: str) -> bool:
    """대상인가 — **종가 기준으로 신고가가 선 종목만** 본다.

    고가 기준은 장중에 잠깐 뚫고 종가는 전일보다 낮게 끝난 날도 신고가로
    잡는다. 그런 종목을 수급 리포트로 받으면 '일간 수익률은 마이너스인데 52주
    신고가' 가 되어 매번 멈춰서 따져 보게 된다. 종가 기준은 그런 날이 없다.
    """
    return _hits(row, "close_basis", kind)


def pick_targets(
    nh: dict[str, Any] | None,
    top: int = TOP_N,
    kind: str = KIND,
    vol_min: float = VOL_MULT_MIN,
) -> tuple[list[tuple[str, str, dict[str, Any]]], dict[str, int]]:
    """대상 고르기. 반환 `([(코드, 이름, 메타)], 집계)`.

    신고가(`kind`)가 선 종목 중 **거래량이 평소보다 는** 것만 남기고, 많이
    는 순서로 `top` 개를 집는다. 평소는 보드가 재 둔 20거래일 평균이다.

    `vol_min` 이 0 이하면 문턱을 걸지 않고 **보드가 매긴 순서**를 그대로 쓴다.
    보드의 정렬은 라벨 우선순위(역사적 > 52주 > 120일) + 거래대금이라, 거래량
    기준으로 다시 세우면 두 화면의 순서가 갈린다. 문턱을 걸 때만 갈라진다.

    `집계` 는 왜 줄었는지를 적는다 — 몇이 라벨에서, 몇이 거래량에서, 몇이
    거래량을 못 구해서 빠졌는지. 조용히 사라지면 '오늘은 없었다' 와 '자료가
    없었다' 가 구분되지 않는다.
    """
    seen: set[str] = set()
    rows: list[tuple[str, str, dict[str, Any]]] = []
    st = dict(achieved=0, kind_out=0, no_vol=0, below=0, passed=0)
    for r in (nh or {}).get("achieved") or []:
        code, name = r.get("code"), r.get("name")
        if not code or code in seen:
            continue
        seen.add(code)
        st["achieved"] += 1
        if kind and kind != "all" and not hit(r, kind):
            st["kind_out"] += 1
            continue
        vm = r.get("vol_mult")
        if vol_min > 0:
            if vm is None:
                # 거래량을 못 구한 종목이다(분할 가드로 구간이 잘렸거나 원자료
                # 결측). '0배' 로 치면 없는 사실을 지어내는 것이라 빼고 센다.
                st["no_vol"] += 1
                continue
            if vm < vol_min:
                st["below"] += 1
                continue
        st["passed"] += 1
        rows.append((code, name or code, meta_of(r, kind)))
    if vol_min > 0:
        # 많이 는 순. 같은 배수면 보드가 매긴 순서를 지킨다(안정 정렬).
        rows.sort(key=lambda t: -(t[2].get("vol_mult") or 0))
    return rows[:top], st


def meta_of(row: dict[str, Any], kind: str = KIND) -> dict[str, Any]:
    """보드가 이미 잰 값을 그대로 옮긴다 — 여기서 다시 계산하지 않는다.

    `turnover_is_estimate` 는 거래대금을 종가x거래량으로 채웠다는 뜻이다(소스가
    거래대금을 안 줄 때). 추정치를 실측처럼 적지 않으려고 같이 들고 온다.
    금액(mktcap·turnover)은 보드 산출 그대로 **억원**이다.
    """
    return dict(
        basis=basis_of(row, kind),  # 고가 / 종가 / 고가·종가
        vol_mult=row.get("vol_mult"),
        mktcap=row.get("mktcap"),  # 억원
        turnover=row.get("turnover"),  # 억원
        turnover_est=bool(row.get("turnover_is_estimate")),
        turnover_avg20=row.get("turnover_avg20"),  # 억원 (종가x거래량 추정)
    )
