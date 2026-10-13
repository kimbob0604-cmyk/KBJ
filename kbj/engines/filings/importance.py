"""공시 중요도 — 제목 키워드 점수(공개)와 관심종목·시총 가산(로그인).

승격 원본: SD `server.py` `_DISCLOSURE_SCORE_TABLE`:9920(점수표 — 정본은 이제 `config/fin.yaml
filings.keywords`), `score_disclosure`:9974, `_cap_bonus_for_code`:9946. docs/p4_design.md §4.1.

규칙(SD 그대로)
- 제목과 키워드 모두 공백을 지우고 '들어 있는가'를 본다.
- 키워드 점수 = 맞은 키워드 점수 중 **최댓값**(합이 아니다 — SD :9986). 맞은 키워드는 표 순서대로
  모두 남긴다(같은 뜻의 두 표기 '최대주주변경'·'최대주주 변경' 은 둘 다 남는다 — SD 와 같다).
- 등급: 총점 ≥ critical(10) → critical, ≥ high(6) → high, ≥ medium(4) → medium, 그 밖 low.
- 로그인 가산(읽을 때 계산 — `pub_*` 에 쓰지 않는다, 설계 §2 혼합 값): 관심종목 +3, 전 거래일 회사
  시총이 경계를 **초과**하면 +3·+2·+1(10조·1조·1,000억). 시총을 모르면 0.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from kbj.config.fin import FilingsCfg, ImportanceLevels

__all__ = [
    "Importance",
    "KeywordScore",
    "LoginScore",
    "importance_of",
    "keyword_score",
    "login_score",
]

Importance = Literal["critical", "high", "medium", "low"]


@dataclass(frozen=True)
class KeywordScore:
    """제목 키워드 점수(공개 — `pub_filings.disclosure.kw_score`·`kw_matched`)."""

    score: int
    matched: tuple[str, ...]


@dataclass(frozen=True)
class LoginScore:
    """로그인 중요도(키워드 + 관심종목 + 시총 가산) — API 가 읽을 때 만든다."""

    keyword: int
    watch_bonus: int
    cap_bonus: int
    total: int
    importance: Importance


def _squash(s: str) -> str:
    return s.replace(" ", "")


def keyword_score(title: str | None, keywords: Mapping[str, int]) -> KeywordScore:
    """제목 → (최고 점수, 맞은 키워드 표 순서). 아무것도 안 맞으면 (0, ())."""
    t = _squash(title or "")
    best = 0
    matched: list[str] = []
    for kw, score in keywords.items():
        k = _squash(kw)
        if k and k in t:
            matched.append(kw)
            best = max(best, score)
    return KeywordScore(best, tuple(matched))


def importance_of(total: int, levels: ImportanceLevels) -> Importance:
    """총점 → 등급(경계 포함)."""
    return levels.level(total)


def login_score(
    kw: KeywordScore | int,
    *,
    is_watch: bool,
    mktcap: int | None,
    cfg: FilingsCfg,
) -> LoginScore:
    """키워드 점수 + 관심종목 가산 + 시총 가산 → 총점·등급(SD `score_disclosure` 의 total)."""
    base = kw.score if isinstance(kw, KeywordScore) else kw
    if isinstance(base, bool) or base < 0:
        raise ValueError(f"키워드 점수는 0 이상의 정수: {base!r}")
    watch = cfg.login_bonus.watchlist if is_watch else 0
    cap = cfg.login_bonus.cap_bonus(mktcap)
    total = base + watch + cap
    return LoginScore(base, watch, cap, total, importance_of(total, cfg.importance_levels))
