"""
보드 하루 계산 — 입력(`BoardInputs`)만으로 그날 산출 다섯 개를 만든다(순수, I/O 없음).

KBJ 정본(docs/p3_design.md §1.3·§4.1, D-P3-5·10). ET `board/engine/build.py` 의 `run`:425~727
에서 DB·파일 I/O(`DB.connect`·`write`·`read`·`conn.executemany`·`DB.log_step`)만 뺀 같은
계산이다 — `compute_day(inp, cfg) -> BoardDay`. 함수 본문은 그대로 옮겼고(골든
`tests/golden/test_board_golden.py` 가 legacy 산출과 같음을 지킨다), 바뀐 것은 다음뿐이다.

- 입력: `DB.trading_days`·`snapshot`·`all_series`·`sector_of`·`alltime`·`split_cleared`·
  `split_unknown`·`missing`·`note` 의 결과와 전일 라벨·전일 newhigh 근접·전일 rankings 를
  `BoardInputs` 로 받는다(읽는 쪽 — legacy `build.run` shim 은 sqlite, kbj 는
  `kbj.services.engine.board` 가 저장소).
- 출력: `write(...)` 대신 payload 를 돌려준다(`BoardDay`). `log` 줄은 `BoardDay.log`, run_log 에
  남기던 실패 단계는 `BoardDay.steps`(읽는 쪽이 기록한다). 배너(`notes`)와 진단(`diagnostics`)을
  나누는 것은 ET :466~474 그대로.
- KRX 확정치를 못 받은 날의 문구는 네이버 대신 "KIS 마감값(잠정)" 이다(골든 비교는 이 한 문장만
  정규화한다 — §4.1). 품질: KRX 확정이면 ok, 아니면 estimated.
- 시각(`generated_at`)은 주입한다(벽시계를 읽지 않는다).

산출(ET state/<날짜>/*.json 과 같은 키):
  universe   전 종목 시세·시총·거래대금 + 업종·테마 매핑
  newhigh    신고가 달성·근접 (이 대시보드의 본체)
  sectors    업종·테마 집계와 히트맵 입력
  events     가격·거래량 기반 탐지기 출력
  rankings   랭킹 표(섹터·종목) — `rankings.build_rankings`
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Final

from kbj.core.quality import Quality
from kbj.core.rows import AllTime, Bar, Snap

from . import aggregate as agg
from . import kinds as K
from . import newhigh as nh
from . import rankings as RK
from . import themes as TH

__all__ = [
    "CLOSE_CONFIRM_MIN",
    "ENGINE_VERSION",
    "KRX_SOURCE",
    "MAX_PREV_GAP_DAYS",
    "PROVISIONAL_CLOSE_PHRASE",
    "BoardDay",
    "BoardInputs",
    "BoardLabel",
    "achieved_rows",
    "by_mktcap",
    "close_label",
    "close_provenance",
    "compute_day",
    "consistency_notes",
    "high_label",
    "prev_near_from",
    "prev_ranks_from",
    "snap_row",
    "unit_sanity",
]

# 산출을 만든 엔진 판(artifact.engine_version). 계산이 바뀌면 올린다.
ENGINE_VERSION: Final = "board-p3.2"  # p3.2: 신고가 3축(ADR 0017)
# KRX 확정치를 못 받은 날 종가의 출처 문구(ET 는 '네이버 16:07 값')
PROVISIONAL_CLOSE_PHRASE: Final = "KIS 마감값(잠정)"


# 스냅샷 source 가 이 값이면 그날 종가는 KRX 정규장 확정치다 (D-080).
# `ingest/krx.py` 의 SOURCE 와 같은 문자열이다 — engine 은 ingest 를 import 하지
# 않는다(단계 사이는 state/ 파일과 DB 로만 오간다). 한쪽을 바꾸면 tests 가 잡는다.
KRX_SOURCE = "krx"

# 유니버스의 이 비율 이상이 KRX 확정치로 덮여야 '종가 확정' 으로 본다.
# 거래정지·신규상장 등으로 몇 종목이 빠지는 것은 정상이라 1.0 을 쓰지 않는다.
CLOSE_CONFIRM_MIN = 0.9


def close_provenance(snap):
    """그날 종가가 KRX 정규장 확정치인가. `(확정, 덮인 종목수, 부분 경고문|None)`.

    **집합 포함(`'krx' in src`)으로 보면 한 종목만 덮여도 보드 전체가 '확정' 이
    된다.** `krx.fetch_day` 는 코스피·코스닥을 차례로 받고 한쪽 응답이 비어도
    예외를 내지 않으므로, 절반만 덮인 채 통과하는 것이 구조상 가능하다.
    그래서 비율로 본다.

    스냅샷의 `source` 가 곧 그날 종가의 출처다 — 수집이 확정치로 덮었으면
    'krx' 가 찍혀 있다. 여기서 DB 를 다시 읽지 않는 이유도 그것이다.

    판정을 함수로 빼 둔 이유: 이 규칙을 시험이 **값으로** 확인할 수 있게 하려는
    것이다. 예전 시험은 build 의 소스 문자열을 단정해서, 규칙을 고치면 버그를
    고쳐도 빨갛게 됐다.
    """
    n_snap = len(snap)
    if not n_snap:
        return False, 0, None
    n_krx = sum(1 for s in snap.values() if (s or {}).get("source") == KRX_SOURCE)
    ok = (n_krx / n_snap) >= CLOSE_CONFIRM_MIN
    partial = None
    if n_krx and not ok:
        partial = (
            f"{n_snap:,}종목 중 {n_krx:,}종목만 KRX 정규장 확정치입니다 "
            f"({n_krx / n_snap * 100:.0f}%). 한 시장만 받았을 수 있습니다 — "
            "보드 전체를 확정으로 보지 않습니다"
        )
    return ok, n_krx, partial


def _ret(rows, idx, n, floor=0):
    """n영업일 전 종가 대비 수익률 %. 이력이 모자라면 None.

    floor 는 수정주가 이상 지점 이후의 시작 인덱스다(newhigh.split_guard).
    이걸 안 보면 5:1 분할 미반영 종목의 5일 수익률이 -80% 로 나오고, 그 값이
    랭킹 표 1위와 섹터 시총가중 등락률에 그대로 들어간다. 신고가 판정만
    보호하고 랭킹은 오염되고 있었다.
    """
    j = idx - n
    if j < floor:
        return None
    a, b = rows[j].get("close"), rows[idx].get("close")
    if not a or not b:
        return None
    return round((b / a - 1) * 100, 2)


# 직전 봉이 이만큼 이상 떨어져 있으면 그 사이 수익률을 '당일 등락률'로 쓰지 않는다.
# 거래정지 15일 뒤 재개한 종목의 +30% 가 당일 등락률로 섹터 평균에 들어가면
# 그 섹터 숫자가 통째로 거짓이 된다.
MAX_PREV_GAP_DAYS = 5


def _chg(ev, snap):
    """당일 등락률. 소스가 준 값을 먼저 쓴다.

    소스(목록 API)는 거래소가 계산한 정확한 전일 대비다. 일봉 파생값은 DB 에
    남아 있는 직전 행 대비라 거래정지 공백을 삼킨다. 맞는 값이 있는데 버릴
    이유가 없다.
    """
    v = snap.get("chg_pct")
    if v is not None:
        return v
    gap = ev.get("prev_gap_days")
    if gap is not None and gap > MAX_PREV_GAP_DAYS:
        return None  # 공백이 길면 당일 등락률로 쓸 수 없다
    return ev.get("chg_pct")


def _chg_src(ev, snap):
    if snap.get("chg_pct") is not None:
        return "source"
    gap = ev.get("prev_gap_days")
    if gap is not None and gap > MAX_PREV_GAP_DAYS:
        return None
    return "bar" if ev.get("chg_pct") is not None else None


# 시총이 1조 넘는 종목이 하루에 1억도 안 사고팔릴 수는 없다. 단위 환산이
# 틀리면 값이 '그럴듯하게 작은' 숫자가 되어 아무 데서도 안 걸린다 — 2026-08-28 에
# 거래대금이 1e6 배 작게 들어와 화면에 '0억' 으로 찍히고 랭킹 표가 통째로 비었다.
# 필드 하나만 보면 못 잡는다. 두 필드의 관계로 본다.
BIG_CAP_EOK = 10000.0  # 1조
MIN_BIG_CAP_TURNOVER = 1.0  # 억원


def unit_sanity(rows):
    """거래대금 단위가 뒤집혔는지 본다. 이상하면 사람이 읽을 사유, 아니면 None."""
    big = [
        r for r in rows if (r.get("mktcap") or 0) >= BIG_CAP_EOK and r.get("turnover") is not None
    ]
    if len(big) < 20:
        return None  # 표본이 적으면 판단하지 않는다
    vals = sorted(r["turnover"] for r in big)
    med = vals[len(vals) // 2]
    if med >= MIN_BIG_CAP_TURNOVER:
        return None
    return (
        f"거래대금 단위 의심 — 시총 1조 이상 {len(big):,}종목의 거래대금 "
        f"중앙값이 {med:,.4f}억이다. 1조 기업이 하루 1억도 거래되지 않는 것은 "
        f"물리적으로 불가능하므로 소스 단위 환산을 확인하라"
    )


def _turnover(rows, idx, snap_val):
    """당일 거래대금(억원).

    소스 목록 API 가 거래대금을 안 주는 경우가 있다 (네이버 실측). 그때는
    일봉의 종가x거래량으로 채운다. 체결가 가중이 아니라 추정치이므로
    호출자가 is_estimate 를 달아야 한다. None 을 그대로 두면 유동성 하한에
    전 종목이 걸려 랭킹 표가 통째로 빈다 — 2026-08-27 실행에서 실제로 그랬다.

    반환 (값, 추정여부).
    """
    r = rows[idx]
    # 거래량이 있는데 거래대금이 0 인 것은 물리적으로 불가능하다. 소스가 키를
    # 빼는 대신 0 을 채워 보내는 경우가 있어(네이버 실측) 여기서도 한 번 더 막는다.
    # 이 방어가 없으면 0 이 '사실'로 통과해 유동성 하한에 전 종목이 걸린다.
    if snap_val is not None and not (snap_val == 0 and r.get("volume")):
        return snap_val, False
    if r.get("close") and r.get("volume") is not None:
        return r["close"] * r["volume"] / 1e8, True
    return None, True


def _vol_ratio(rows, idx, short=3, long=20, floor=0):
    """단기 평균 거래량 / 장기 평균 거래량, %.

    사용자 엑셀의 '거래량 3일/1달' 열이다. 거래량 배수(당일/20일)와 다르다.
    하루짜리 튐이 아니라 **며칠째 붙어 있는지**를 본다. 하루만 터진 종목은
    3일 평균이 희석돼 이 값이 덜 오르고, 사흘 연속이면 크게 오른다.
    """
    if idx - long + 1 < floor:
        return None  # 이상 지점 이전 구간을 섞지 않는다
    a = rows[max(floor, idx - short + 1) : idx + 1]
    b = rows[max(floor, idx - long + 1) : idx + 1]
    va = [r["volume"] for r in a if r.get("volume") is not None]
    vb = [r["volume"] for r in b if r.get("volume") is not None]
    if not va or not vb:
        return None
    m = sum(vb) / len(vb)
    if not m:
        return None
    return round((sum(va) / len(va)) / m * 100, 1)


def _turnover_avg20(rows, idx, n=20, floor=0):
    """20일 평균 거래대금(억원) 추정. 일봉에 거래대금이 없어 종가x거래량으로 만든다.

    추정치이므로 이 값을 쓰는 필드에는 is_estimate 를 단다 (CLAUDE.md 2장 1번).
    """
    if idx - n < floor:
        return None
    win = rows[max(floor, idx - n) : idx]
    vals = [
        (r["close"] or 0) * (r["volume"] or 0) / 1e8
        for r in win
        if r.get("close") and r.get("volume") is not None
    ]
    return sum(vals) / len(vals) if vals else None


def close_label(r):
    return (r.get("close_basis") or {}).get("label")


def high_label(r):
    return (r.get("high_basis") or {}).get("label")


# 한 줄에 적을 위반 예시 수. 전부 적으면 배너가 표가 된다.
CONSIST_EX = 3


def consistency_notes(rows, cfg):
    """산출물이 스스로와 맞는지 본다.

    이 프로젝트를 반복해서 문 것은 '필드 하나만 보면 멀쩡한데 서로 안 맞는'
    부류다 — 거래대금 단위(D-035), 밀린 열(D-049), 낡은 과거 구간(D-056).
    값 하나를 보는 검사로는 하나도 못 잡았고, 전부 교차 검사가 잡았다.

    여기서 보는 것은 정의가 강제하는 관계뿐이다. 임계값 튜닝이 아니라
    '이게 어긋나면 계산이 틀린 것' 인 것들이다 (CLAUDE.md 4장).

      1. 상위 라벨이면 하위도 갱신이다 — 상위가 하위를 포함한다
      2. 갱신한 종류의 갭은 0 이하다 — 이미 넘겼다는 뜻이니까
      3. label 은 갱신한 것 중 최상위다
      4. 근접은 갭 임계 안에 있다
      5. 역사적 최고가 >= 모든 창의 최고가 — 상장 이후가 직전 52주를 포함한다

    5번이 1번의 원인 자리다. 1번이 걸린 090410 을 두 번 추측하고 두 번 틀린 뒤,
    갭이 아니라 기준값을 보고서야 원인이 드러났다 — 역사적 기준이 52주 기준보다
    42% 낮았다. 갭은 결과고 기준값이 원인이다. 원인 쪽을 직접 본다.

    **1번은 계산된 종류끼리만 본다.** `hits[k]` 는 '갱신 못 했다' 와 '계산하지
    못했다' 를 둘 다 False 로 적는다. 최근 상장 종목은 52주 창을 못 채워
    w52 를 계산하지 못하는데, 역사적 최고가는
    스칼라가 들고 있어 hist 는 판정된다. 그러면 'hist 인데 w52 아님' 이 되지만
    이건 모순이 아니라 창이 짧은 것이다.

    첫 실행에서 이걸로 090410 이 위반으로 찍혔다. 엔진이 아니라 이 검사가
    틀렸다. `gap[k]` 가 None 인지로 '계산했는가' 를 가른다 — 기준 최고가가
    없으면 갭도 None 이다.

    반환: 사람이 읽는 사유 목록. 비어 있으면 이상 없음.
    """
    pri = cfg["newhigh"]["priority"]
    rank = {k: i for i, k in enumerate(pri)}
    mx = cfg["proximity"]["max_gap_pct"]
    bad = {}

    def hit(kind, code, detail=""):
        # 코드만 적으면 "그렇다" 는 알아도 "무엇이" 는 모른다. 오늘 아침
        # HTTP 403 이 정확히 그 모양이었다 — 상태코드만 있고 사유가 없었다.
        bad.setdefault(kind, []).append(f"{code} {detail}".strip())

    for r in rows:
        hits = r.get("hits") or {}
        gaps = r.get("gap") or {}
        on = [k for k in pri if hits.get(k)]
        if on:
            top = min(on, key=lambda k: rank[k])
            # 계산된 종류만 본다. 갭이 None 이면 기준 최고가가 없어 판정 자체를
            # 못 한 것이고, 그건 모순이 아니라 창이 짧은 것이다.
            miss = [k for k in pri[rank[top] + 1 :] if not hits.get(k) and gaps.get(k) is not None]
            if miss:
                hit(
                    "상위 라벨인데 하위가 미갱신",
                    r["code"],
                    f"{top} 인데 " + ", ".join(f"{k} 갭 {gaps[k]:+.2f}%" for k in miss),
                )
            if r.get("label") != top:
                hit("라벨이 최상위가 아님", r["code"], f"label={r.get('label')} 최상위={top}")
            over = [k for k in on if (g := gaps.get(k)) is not None and g > 0.01]
            if over:
                hit(
                    "갱신했는데 갭이 양수",
                    r["code"],
                    ", ".join(f"{k} {gaps[k]:+.2f}%" for k in over),
                )
        elif r.get("label"):
            hit("갱신이 없는데 라벨이 있음", r["code"], f"label={r['label']}")
        # 5. 기준값 자체의 포함관계. hist 는 상장 이후 전체이므로 어떤 창의
        #    최고가보다도 작을 수 없다. 작다면 스칼라와 일봉이 서로 다른
        #    시계열을 가리키는 것이고, 그러면 위의 라벨 판정이 통째로 어긋난다.
        refs = r.get("refs") or {}
        h = refs.get("hist")
        if h:
            lower = [(k, v) for k, v in refs.items() if k != "hist" and v and v > h * (1 + 1e-9)]
            if lower:
                hit(
                    "역사적 최고가가 창 최고가보다 낮음",
                    r["code"],
                    f"hist {h:,.0f} < " + ", ".join(f"{k} {v:,.0f}" for k, v in sorted(lower)),
                )

        nk = r.get("near_kind")
        if nk:
            g = gaps.get(nk)
            if g is None or not (0 <= g <= mx + 1e-6):
                hit(
                    "근접인데 갭이 임계 밖",
                    r["code"],
                    f"{nk} 갭 {'없음' if g is None else f'{g:+.2f}%'} (임계 {mx}%)",
                )

    out = []
    for why, codes in sorted(bad.items()):
        ex = " / ".join(codes[:CONSIST_EX])
        out.append(
            f"산출물 정합성 위반 — {why} {len(codes):,}종목: {ex}. 신고가 판정 계산을 확인하세요"
        )
    return out


def by_mktcap(rows, cap_min):
    """보드에 올릴 행만 남긴다. 반환 (행, 하한미달 수, 시총모름 수).

    시총 하한은 **화면 토글이 아니라 여기서** 건다. 토글로 두면 newhigh.json 에는
    남아 있어서 엑셀·텔레그램·아티팩트가 각각 다른 목록을 들고 나간다 (D-072).

    하한 미달과 **값을 모르는 것**을 따로 센다. 합쳐 세면 소스가 시총을 안 준
    날에도 '하한으로 걸렀다' 는 회색 안내만 나가고 진짜 실패가 묻힌다 (규칙 1).
    """
    if not cap_min:
        return list(rows), 0, 0
    keep, small, unknown = [], 0, 0
    for r in rows:
        cap = r.get("mktcap")
        if cap is None:
            unknown += 1
        elif cap < cap_min:
            small += 1
        else:
            keep.append(r)
    return keep, small, unknown


def achieved_rows(rows, show, rank):
    """달성 목록 — **두 기준의 합집합**.

    고가 기준으로 거른 뒤 화면에서 종가 라벨로 다시 걸러 내면, 종가로만 신고가를
    낸 종목이 조용히 사라진다. 그런 종목은 얼마든지 있다 — 종가 기준의 참조
    최고가(과거 종가들의 최고)는 고가 기준의 참조 최고가(과거 고가들의 최고)보다
    낮으므로, 종가로는 뚫고 고가로는 못 뚫는 날이 나온다.

    필터는 화면이 하고 데이터는 두 기준을 다 담는다.
    """

    def _rank(r):
        # 정렬은 **기본 기준**(default_basis) 우선. 그 라벨이 없으면 다른 기준의
        # 등급으로. 어느 쪽이 기본인지는 설정이 정한다.
        return rank.get(r.get("label"), rank.get(close_label(r), rank.get(high_label(r), 99)))

    # 두 기준을 **이름으로** 본다. 기본 기준(label)만 보면, 기본이 종가일 때
    # 고가로만 뚫은 종목이 목록에서 통째로 빠져 화면의 '고가 기준' 토글이 빈
    # 표가 된다. 거르기는 합집합이고 고르기는 화면이 한다.
    out = [r for r in rows if high_label(r) in show or close_label(r) in show]
    out.sort(key=lambda r: (_rank(r), -(r.get("turnover") or 0)))
    return out


# ─────────────────────────── 입력·출력 ───────────────────────────
def _iso(d: date | str | None) -> str | None:
    if d is None:
        return None
    return d.isoformat() if isinstance(d, date) else str(d)


def snap_row(s: Mapping[str, Any] | Snap) -> dict[str, Any]:
    """스냅 한 줄을 엔진 모양 dict 로. `Snap` 의 금액(원)은 **억원** 으로 바꾼다(엔진 안 관례 —
    ET snap 표가 억원이었다). dict 는 그대로 복사한다(골든 입력)."""
    if isinstance(s, Snap):
        return dict(
            code=s.code,
            asof=s.date.isoformat(),
            name=s.name,
            market=s.market,
            close=s.close,
            chg_pct=s.chg_pct,
            volume=s.volume,
            turnover=None if s.turnover is None else s.turnover / 1e8,
            mktcap=None if s.mktcap is None else s.mktcap / 1e8,
            turnover_is_estimate=1 if s.turnover_is_estimate else 0,
            source=s.source,
        )
    return dict(s)


def prev_near_from(newhigh: Mapping[str, Any] | None) -> dict[str, tuple[Any, Any]]:
    """전일 newhigh 의 근접 목록 → {code: (근접 기준, 갭)} — 탐지기 10(돌파 실패)의 입력.

    어제 어떤 기준으로 근접이었는지까지 넘긴다. 기준이 바뀌면 서로 다른 기준값의 갭을 빼게 되므로
    비교가 무의미해진다(ET build.run :705 그대로)."""
    return {
        x["code"]: (x.get("near_kind"), x.get("near_gap"))
        for x in (newhigh or {}).get("proximity", [])
    }


def prev_ranks_from(labels: Mapping[str, Any]) -> dict[str, int]:
    """전일 라벨 {code: 행(`rank` 속성 또는 키)} → {code: rank}."""
    out: dict[str, int] = {}
    for code, lab in labels.items():
        rank = lab["rank"] if isinstance(lab, Mapping) else lab.rank
        out[code] = int(rank)  # pyright: ignore[reportUnknownArgumentType]
    return out


@dataclass(frozen=True)
class BoardInputs:
    """그날 보드를 계산하는 데 필요한 것 전부(docs/p3_design.md §4.1 '입력·출력').

    asof            기준일(일봉의 마지막 날)            prev_asof   직전 거래일(없으면 None)
    series          {code: 오름차순 일봉(asof 까지)} — ET dict 행 또는 `Bar`
    snapshot        {code: 스냅} — ET dict(억원) 또는 `Snap`(원 → 억원으로 바꿔 읽는다)
    snap_asof       스냅의 실제 날짜(asof 이하 가장 최근 — 다르면 '스냅 늦음' 안내)
    alltime         {code: 역사적 최고가 스칼라} — 그날 `roll_alltime` 반영 뒤(수집 → 갱신 → 계산)
    sectors         {code: 1층 섹터(board48)}
    taxonomy_counts [(분류 체계, 종목 수)] 많은 순
    prev_ranks      전일 기본 기준 라벨 {code: rank}(비면 신규/이어감을 판정하지 않는다)
    split_cleared   공시 대조로 '분할이 아니다' 가 확인된 코드(verdict none)
    split_unknown   공시 대조를 못 한 [(code, note)]
    prev_near       전일 근접 {code: (기준, 갭)} — `prev_near_from(전일 newhigh)`
    collect_notes   수집 단계가 남긴 실패 [(step, note)] — 배너로 올라간다
    close_note      KRX 확정치를 못 받은 사유(ET run_log 'close_krx')
    funds_excluded  ETF·ETN 제외 기록(ET run_log 'funds_excluded' — 없으면 '필터가 돌지 않았을 수')
    themes_yaml     2층 테마 사전
    taxonomy        1층 분류 사전(sectors.yaml)
    prev_rankings   전일 rankings payload(순위 변동)
    market          market.json(kbj 에는 없다 — None)
    listed_on       {code: 상장일} — 스칼라에 history_from 이 있을 때 역사적 신고가 깊이 판정
                    (D-P3-11). None 이면 상장일을 모르는 것으로 본다
    prev_incomparable  전일 라벨과 비교할 수 없는 사유(전일 보드가 다른 신고가 정의 — ADR 0017).
                    있으면 신규/이어감을 판정하지 않고 이 사유를 안내에 싣는다(전일 라벨이 없다는
                    문구 대신)
    trading_days    시장 거래일 달력(asof 이하 — 신고가 거래일 창의 경계, ADR 0017). None 이면 전
                    종목 일봉 날짜의 합집합(그날 한 종목이라도 거래했으면 시장 거래일 — 골든·legacy)
    generated_at    주입한 시계의 시각(ISO 문자열)
    """

    asof: date | str
    prev_asof: date | str | None
    series: Mapping[str, Sequence[Mapping[str, Any] | Bar]]
    snapshot: Mapping[str, Mapping[str, Any] | Snap]
    snap_asof: date | str | None
    generated_at: str
    alltime: Mapping[str, Mapping[str, Any] | AllTime] = field(
        default_factory=dict[str, Mapping[str, Any] | AllTime]
    )
    sectors: Mapping[str, str] = field(default_factory=dict[str, str])
    taxonomy_counts: Sequence[tuple[str, int]] = ()
    prev_ranks: Mapping[str, int] = field(default_factory=dict[str, int])
    split_cleared: frozenset[str] = frozenset()
    split_unknown: Sequence[tuple[str, str]] = ()
    prev_near: Mapping[str, tuple[Any, Any]] = field(default_factory=dict[str, tuple[Any, Any]])
    collect_notes: Sequence[tuple[str, str]] = ()
    close_note: str | None = None
    funds_excluded: str | None = None
    themes_yaml: Mapping[str, Any] = field(default_factory=dict[str, Any])
    taxonomy: Mapping[str, Any] = field(default_factory=dict[str, Any])
    prev_rankings: Mapping[str, Any] | None = None
    market: Mapping[str, Any] | None = None
    listed_on: Mapping[str, date | str | None] | None = None
    trading_days: Sequence[date | str] | None = None
    prev_incomparable: str | None = None


@dataclass(frozen=True)
class BoardLabel:
    """그날 라벨 한 줄(ET label 표 — 기본 기준과 종가 기준)."""

    code: str
    basis: str
    kind: str
    rank: int


@dataclass(frozen=True)
class BoardDay:
    """그날 보드 산출. payload 다섯 개는 ET state JSON 과 같은 키다."""

    asof: str
    universe: dict[str, Any]
    newhigh: dict[str, Any]
    sectors: dict[str, Any]
    events: dict[str, Any]
    rankings: dict[str, Any]
    labels: tuple[BoardLabel, ...]
    notes: tuple[str, ...]
    diagnostics: tuple[str, ...]
    steps: tuple[tuple[str, str], ...]  # run_log 에 실패로 남길 (단계, 사유) — 읽는 쪽이 기록
    log: tuple[str, ...]
    quality: Quality  # KRX 확정이면 ok, KIS 마감(잠정)이면 estimated
    close_confirmed: bool
    source: str
    hist_scope: dict[str, Any]  # 역사적 신고가 계산 범위(D-P3-11 — history_from)

    def payloads(self) -> dict[str, dict[str, Any]]:
        """{이름: payload} — 이름은 ET 파일 이름(확장자 없이)."""
        return dict(
            universe=self.universe,
            newhigh=self.newhigh,
            sectors=self.sectors,
            events=self.events,
            rankings=self.rankings,
        )


def _hist_scope(
    alltime: Mapping[str, Mapping[str, Any]],
    codes: Sequence[str],
    hist_blocked: Mapping[str, int],
    source_floor: Any = None,
    n_since_floor: int = 0,
) -> dict[str, Any]:
    """역사적 신고가 계산 범위(진단 — D-P3-11·ADR 0017). 화면은 날짜를 '역사적 = 그 날 이후' 로
    내세우지 않는다: 보류 수와 바닥 기준 수만 품질 메모로 적는다."""
    hf = sorted(
        str(h) for c in codes if (h := (alltime.get(c) or {}).get("history_from")) is not None
    )
    return dict(
        history_from=hf[0] if hf else None,
        history_from_latest=hf[-1] if hf else None,
        n_with_history_from=len(hf),
        n_before_listing=hist_blocked.get(nh.HIST_BEFORE_LISTING, 0),
        n_listing_unknown=hist_blocked.get(nh.HIST_LISTING_UNKNOWN, 0),
        source_floor=None if source_floor is None else str(source_floor),
        n_since_floor=n_since_floor,
    )


# ─────────────────────────── 하루 계산 ───────────────────────────
def compute_day(inp: BoardInputs, cfg: Mapping[str, Any]) -> BoardDay:
    """그날 보드(ET `build.run` 의 계산 — I/O 없음)."""
    asof = _iso(inp.asof)
    if not asof:
        raise RuntimeError("기준일이 비었다. 먼저 일봉을 적재하라.")
    prev_asof = _iso(inp.prev_asof)
    basis = cfg["newhigh"]["default_basis"]
    lines: list[str] = []
    log: Callable[[str], object] = lines.append
    steps: list[tuple[str, str]] = []

    snap = {code: snap_row(s) for code, s in inp.snapshot.items()}
    snap_asof = _iso(inp.snap_asof)
    if not snap:
        raise RuntimeError(
            f"기준일 {asof} 이하의 종목 스냅샷이 없다. ingest 를 먼저 돌려라. "
            "(조용히 빈 리포트를 내지 않는다 — CLAUDE.md 2장 6번)"
        )
    stale = None
    if snap_asof != asof:
        stale = (
            f"종목 스냅샷이 {snap_asof} 자다 (일봉 기준일 {asof}). "
            "시가총액·거래대금이 하루 어긋났을 수 있다"
        )
    series = {
        code: [r for r in nh.as_rows(rows) if r["asof"] <= asof]
        for code, rows in inp.series.items()
    }
    sectors = inp.sectors
    alltime: dict[str, Mapping[str, Any]] = {
        code: nh.alltime_dict(a) if isinstance(a, AllTime) else a for code, a in inp.alltime.items()
    }
    prev_rank = dict(inp.prev_ranks)
    # 어제 라벨 테이블이 통째로 비었으면 '신규'라고 말할 근거가 없다.
    # --init 직후 첫 --daily 는 전 종목이 신규로 나가는데 그건 사실이 아니다.
    has_prev = bool(prev_asof) and bool(prev_rank) and not inp.prev_incomparable

    ty = inp.themes_yaml
    mapping, tmeta, unresolved = TH.build(ty, snap, cfg)
    # 공시 대조로 '분할이 아니다' 가 확인된 종목. 조회에 실패한 것은 안 들어온다.
    cleared = set(inp.split_cleared)
    # 공시를 못 물어본 종목. 가드는 유지되지만 사유가 '공시가 있어서' 가 아니라
    # '물어보지 못해서' 다. 같은 말로 적으면 안 된다 (D-057).
    unknown_split = [(c, n) for c, n in inp.split_unknown]
    listed = inp.listed_on
    # 원천 바닥(가장 이른 봉) — 이력이 여기 닿았으면 상장일이 더 앞이어도 hist 를 판정한다(ADR 0017)
    source_floor = cfg["newhigh"].get("hist_source_floor")
    n_since_floor = 0
    # 신고가 거래일 창의 경계 — 시장 거래일 달력(ADR 0017). 종목 봉 날짜로 잡으면 거래정지로 창이
    # 늘어난다
    cal = nh.market_days(
        inp.trading_days
        if inp.trading_days is not None
        else (r["asof"] for rows in series.values() for r in rows)
    )
    cal = nh.MarketDays(d for d in cal.days if d <= asof)

    rows, labels, suspects = [], [], []
    # 읽는 사람에게 갈 **결손**과 고치는 사람에게 갈 **진단**을 처음부터 나눈다(ET :466~474).
    # 진단도 버리지 않는다 — universe 의 `diagnostics` 와 실행 기록에 그대로 남는다.
    no_series, hist_blocked, missing_notes = [], {}, []
    diag_notes = []
    for code, s in snap.items():
        ser = series.get(code)
        if not ser:
            no_series.append(code)
            continue
        at = alltime.get(code) or {}
        lo = None if listed is None else listed.get(code)
        hist_ref, why = nh.hist_ref_for(at, asof, listed_on=lo, source_floor=source_floor)
        if nh.hist_depth(at, lo, source_floor)[0] == "floor":
            n_since_floor += 1
        if why:
            hist_blocked[why] = hist_blocked.get(why, 0) + 1
        ev = nh.evaluate(
            ser,
            asof,
            cfg,
            hist_ref=hist_ref,
            hist_days=at.get("n_days"),
            split_cleared=code in cleared,
            calendar=cal,
        )
        if not ev:
            no_series.append(code)
            continue
        b = ev["basis"][basis]
        idx = len(ser) - 1
        near = nh.proximity_kind(ev, basis, cfg)
        if near and (s.get("mktcap") or 0) < cfg["proximity"]["min_mktcap_eok"]:
            near = None  # 근접은 시총 하한을 함께 본다
        floor = ev.get("split_floor") or 0
        turnover, turn_est = _turnover(ser, idx, s.get("turnover"))
        prim = TH.primary(mapping, code)
        if ev["suspect"]:
            suspects.append(
                dict(
                    code=code,
                    name=s.get("name"),
                    date=ev["suspect_date"],
                    note=ev["suspect_note"],
                )
            )
        rows.append(
            dict(
                code=code,
                name=s.get("name"),
                market=s.get("market"),
                # 보통주 / 우선주 / 스팩 / 리츠. 빼지 않고 표시만 붙인다 — 화면에서
                # 끄고 켠다. 판정 근거는 kinds.py 문서화 문자열에 있다.
                kind=K.of(code, s.get("name")),
                sector=sectors.get(code) or agg.UNMAPPED,
                theme=(prim or {}).get("theme"),
                theme_name=(prim or {}).get("name"),
                stage=(prim or {}).get("stage"),
                themes=[m["theme"] for m in mapping.get(code) or []],
                close=ev["close"],
                high=ev["high"],
                open=ev["open"],
                low=ev["low"],
                chg_pct=_chg(ev, s),
                chg_pct_source=_chg_src(ev, s),
                prev_gap_days=ev.get("prev_gap_days"),
                volume=ev["volume"],
                vol_mult=ev["vol_mult"],
                avg_vol_20=ev["avg_vol_20"],
                giveback=ev["giveback"],
                giveback_pp=ev["giveback_pp"],
                high_chg_pct=ev["high_chg_pct"],
                turnover=turnover,
                turnover_is_estimate=turn_est,
                turnover_avg20=_turnover_avg20(ser, idx, floor=floor),
                turnover_avg20_is_estimate=True,  # 종가x거래량 추정 (2장 1번)
                mktcap=s.get("mktcap"),
                ret_2d=_ret(ser, idx, 2, floor),
                ret_5d=_ret(ser, idx, 5, floor),
                ret_7d=_ret(ser, idx, 7, floor),
                ret_10d=_ret(ser, idx, 10, floor),
                ret_21d=_ret(ser, idx, 21, floor),
                ret_63d=_ret(ser, idx, 63, floor),
                ret_126d=_ret(ser, idx, 126, floor),
                ret_250d=_ret(ser, idx, 250, floor),
                vol_3d_1m=_vol_ratio(ser, idx, floor=floor),
                label=b["label"],
                hits=b["hit"],
                gap=b["gap"],
                refs=b["refs"],
                narrow5=b["narrow5"],
                resistance=b["resistance"],
                resistance_label=b["resistance_label"],
                near_kind=near,
                near_gap=b["gap"].get(near) if near else None,
                near_narrow5=b["narrow5"].get(near) if near else None,
                status=(nh.continuity(b["rank"], prev_rank.get(code)) if has_prev else None),
                status_unknown=not has_prev,
                suspect=ev["suspect"],
                usable_days=ev["usable_days"],
                # 이력이 상장일까지 닿지 않아(또는 상장일을 몰라) 역사적 신고가를 판정하지 않은
                # 종목의 품질 안내(D-P3-11·ADR 0017). 다른 사유(스칼라 없음 등)는 싣지 않는다
                hist_note=why if why in (nh.HIST_BEFORE_LISTING, nh.HIST_LISTING_UNKNOWN) else None,
                # 두 기준을 **이름으로** 같은 줄에 싣는다. label/hits 는 기본 기준
                # (default_basis)이고 그게 무엇인지는 설정에 달렸다.
                close_basis=dict(
                    label=ev["basis"]["close"]["label"],
                    hits=ev["basis"]["close"]["hit"],
                    gap=ev["basis"]["close"]["gap"],
                ),
                high_basis=dict(
                    label=ev["basis"]["high"]["label"],
                    hits=ev["basis"]["high"]["hit"],
                    gap=ev["basis"]["high"]["gap"],
                ),
            )
        )
        if b["label"]:
            labels.append(BoardLabel(code, basis, b["label"], b["rank"]))
        cb = ev["basis"]["close"]
        if cb["label"]:
            labels.append(BoardLabel(code, "close", cb["label"], cb["rank"]))
    # ET 는 INSERT OR REPLACE 로 (code, asof, basis) 마다 한 줄 — 기본 기준이 종가면 두 줄이 같다
    labels = list({(x.code, x.basis): x for x in labels}.values())

    # ── universe ──────────────────────────────────────────
    # 분류 체계 혼재는 **여기서** 센다(universe 를 만들기 전에 — ET 의 순서).
    taxes = list(inp.taxonomy_counts)
    tax = taxes[0] if taxes else None
    if len(taxes) > 1:
        mix = " / ".join(f"{r[0]} {r[1]:,}" for r in taxes)
        missing_notes.append(
            f"섹터 분류 체계가 섞여 있습니다 — {mix}. --classify 를 전 종목에 다시 돌리세요"
        )
    src = sorted({str(s["source"]) for s in snap.values() if s.get("source")})
    prov = dict(source="+".join(src) or "unknown", as_of=asof, generated_at=inp.generated_at)
    # 종가가 KRX 정규장 확정치인지 (D-080). 스냅샷의 source 가 곧 그날 종가의 출처다.
    close_confirmed, n_krx, partial = close_provenance(snap)
    prov_close = dict(
        close_source=KRX_SOURCE if close_confirmed else prov["source"],
        close_confirmed=close_confirmed,
        close_krx_n=n_krx,
        close_snap_n=len(snap),
    )
    if partial:
        missing_notes.append(partial)
    if not close_confirmed:
        why = inp.close_note or "사유가 기록되지 않았습니다"
        note = (
            f"종가가 잠정치입니다 — KRX 정규장 확정 시세를 못 받아 {PROVISIONAL_CLOSE_PHRASE}"
            f"으로 냈습니다({why}). 이 값은 NXT 애프터마켓·시간외 "
            "단일가가 끝날 때까지 움직이므로 종가 기준 라벨이 이튿날 바뀔 "
            "수 있습니다. 고가 기준은 영향을 받지 않습니다"
        )
        missing_notes.append(note)
        steps.append(("close_basis", note))
        log(f"  경고 — {note}")
    excluded = inp.funds_excluded
    if stale:
        steps.append(("snapshot", stale))
        log(f"  경고 — {stale}")
    if not has_prev and rows:
        note = (
            f"전일({prev_asof or '없음'}) 라벨이 없어 신규/이어감을 판정하지 "
            "못했습니다. 첫 실행이면 정상이고, 다음 영업일부터 나옵니다"
            if not inp.prev_incomparable
            else f"신규/이어감을 판정하지 못했습니다 — {inp.prev_incomparable}"
        )
        missing_notes.append(note)
        log(f"  {note}")
    for note in consistency_notes(rows, cfg):
        # 엔진이 자기 산출을 의심하는 줄이다. 고치는 사람에게 할 말이지
        # 리포트를 읽는 사람에게 할 말이 아니다.
        diag_notes.append(note)
        steps.append(("consistency", note))
        log(f"  경고 — {note}")
    if unknown_split:
        # 지금 표에 남아 있는 종목만 적는다. 상장폐지된 옛 기록까지 세면
        # 매일 줄지 않는 숫자가 배너에 남는다.
        live = [(c, n) for c, n in unknown_split if c in snap]
        if live:
            ex = ", ".join(f"{c}({n.split(':')[-1].strip()})" for c, n in live[:3])
            # 읽는 사람이 알아야 할 것은 '이 종목들의 역사적 신고가는 이 표에
            # 없다' 이고, 왜 못 넣었는지까지다. '판정' 은 내부 용어라 쓰지 않는다.
            missing_notes.append(
                f"{len(live)}종목은 역사적 신고가를 내지 못했습니다 — 주가에 계단이 "
                f"있는데 액면분할·병합 공시를 대조하지 못했습니다: {ex}"
            )
    unit_note = unit_sanity(rows)
    if unit_note:
        diag_notes.append(unit_note)
        steps.append(("units", unit_note))
        log(f"  경고 — {unit_note}")

    # 수집 단계가 남긴 실패 사유를 배너로 올린다(ET DB.missing). 이 계산이 넣은 사유는 이미 위에
    # 있으므로 뺀다. `step` 은 단계 이름이라 읽는 사람에게 뜻이 없다 — 사유만 싣고 단계는 진단에.
    _own = {"close_basis", "consistency", "units", "snapshot"}
    for step, cnote in inp.collect_notes:
        if step in _own or not cnote:
            continue
        if cnote not in missing_notes:
            missing_notes.append(cnote)
        diag_notes.append(f"{step}: {cnote}")

    universe = dict(
        **prov,
        **prov_close,
        n=len(rows),
        funds_excluded=excluded,
        snapshot_asof=snap_asof,
        snapshot_stale=stale,
        unmapped_theme=sum(1 for r in rows if not r["themes"]),
        unmapped_sector=sum(1 for r in rows if r["sector"] == agg.UNMAPPED),
        theme_seeds_unresolved=unresolved,
        adjusted_price_suspects=suspects,
        split_check_unknown=[dict(code=c, note=n) for c, n in unknown_split],
        no_price_series=no_series,
        notes=missing_notes,
        diagnostics=diag_notes,
        hist_not_evaluated=hist_blocked,
        stocks=rows,
    )

    # ── newhigh ───────────────────────────────────────────
    d = cfg["display"]
    show = nh.displayable(cfg)
    rank = nh.rank_of(cfg)
    cap_min = float(d.get("min_mktcap_eok") or 0)
    board_rows, small, cap_unknown = by_mktcap(rows, cap_min)
    ach = achieved_rows(board_rows, show, rank)
    near = [r for r in board_rows if r["near_kind"]]
    near.sort(key=lambda r: (r.get("near_narrow5") is None, r.get("near_narrow5") or 0))
    # 알약의 수는 **표에 실린 모집단**으로 센다. 전 종목으로 세면 시총 하한에
    # 걸린 종목이 숫자에만 남아 알약과 표의 행수가 어긋난다.
    counts = {k: sum(1 for r in board_rows if (r["hits"] or {}).get(k)) for k in nh.kinds(cfg)}

    def _counts(key: str) -> dict[str, int]:
        return {
            k: sum(1 for r in board_rows if ((r.get(key) or {}).get("hits") or {}).get(k))
            for k in nh.kinds(cfg)
        }

    # 기준별 수를 이름으로 싣는다. counts 는 기본 기준의 수라 설정을 바꾸면
    # 가리키는 기준이 바뀐다 — 알약은 counts_high/counts_close 를 쓴다.
    counts_close, counts_high = _counts("close_basis"), _counts("high_basis")
    newhigh = dict(
        **prov,
        **prov_close,
        basis=basis,
        prev_asof=prev_asof,
        thresholds=dict(
            proximity=cfg["proximity"],
            lookback_trading_days=dict(cfg["newhigh"].get(nh.TRADING_KEY) or {}),
            lookback_calendar_days=dict(cfg["newhigh"].get(nh.CALENDAR_KEY) or {}),
        ),
        labels=cfg["newhigh"]["labels"],
        priority=cfg["newhigh"]["priority"],
        displayed=sorted(show, key=lambda k: rank[k]),
        counts=counts,
        counts_close=counts_close,
        counts_high=counts_high,
        n_suspect=len(suspects),
        # 가드가 걸렸다가 공시 대조로 풀린 종목 수(D-056).
        n_split_cleared=sum(1 for r in rows if r["code"] in cleared),
        n_hist_not_evaluated=sum(hist_blocked.values()),
        # 보드에 올리기 전에 시총으로 거른 수. 배너가 이 둘을 다른 색으로 적는다.
        min_mktcap_eok=cap_min,
        # 화면의 거래대금 토글이 쓰는 값. 문구와 판정이 같은 데서 나오게 함께 싣는다.
        min_turnover_eok=float(d.get("min_turnover_eok") or 0),
        n_below_mktcap=small,
        n_mktcap_unknown=cap_unknown,
        achieved=ach[: d["max_rows_achieved"]],
        proximity=near[: d["max_rows_proximity"]],
    )

    # ── sectors ───────────────────────────────────────────
    # 가장 많은 분류 체계를 파일 라벨로 쓰고, 섞였으면 위에서 알렸다.
    sec = agg.by_sector(rows)
    thm = agg.by_theme(rows, mapping, tmeta)
    sectors_p = dict(
        **prov,
        taxonomy_layer1=tax[0] if tax else None,
        sectors=sec,
        themes=thm,
        heatmap_theme=agg.heatmap(rows, thm, cfg),
        heatmap_sector=agg.heatmap(rows, sec, cfg),
    )

    # ── events ────────────────────────────────────────────
    events = dict(
        **prov,
        events=agg.detect(rows, cfg, dict(inp.prev_near)),
        note="뉴스·수급·전일 주장을 요구하는 탐지기(1·2·5·7·8)는 입력 데이터 미확보로 미포함",
    )

    # ── rankings ──────────────────────────────────────────
    rankings = RK.build_rankings(
        universe,
        inp.prev_rankings,
        cfg,
        inp.taxonomy,
        asof=asof,
        generated_at=inp.generated_at,
        newhigh=newhigh,
        sectors=sectors_p,
        market=inp.market,
        log=log,
    )

    lab = cfg["newhigh"]["labels"]
    tally = " / ".join(
        f"{lab.get(k, k)} {counts.get(k, 0)}" for k in sorted(show, key=lambda k: rank[k])
    )
    log(
        f"  기준일 {asof} · 종목 {len(rows):,} · 신고가 {tally} · "
        f"근접 {len(near)} · 수정주가 의심 {len(suspects)}"
    )
    return BoardDay(
        asof=asof,
        universe=universe,
        newhigh=newhigh,
        sectors=sectors_p,
        events=events,
        rankings=rankings,
        labels=tuple(labels),
        notes=tuple(missing_notes),
        diagnostics=tuple(diag_notes),
        steps=tuple(steps),
        log=tuple(lines),
        quality=Quality.OK if close_confirmed else Quality.ESTIMATED,
        close_confirmed=close_confirmed,
        source=prov["source"],
        hist_scope=_hist_scope(alltime, list(snap), hist_blocked, source_floor, n_since_floor),
    )
