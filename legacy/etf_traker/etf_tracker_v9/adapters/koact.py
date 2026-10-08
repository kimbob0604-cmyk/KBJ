"""삼성액티브 KoAct 어댑터 — KBJ 정본 `kbj.data.private.etf_issuers.koact.KoAct` 를 쓰는 shim(P3 묶음 E3).

파싱·HTTP·실측 주의(원문 머리말)는 kbj 쪽으로 옮겼다(두 벌 금지). 여기는 tracker 의 옛
인터페이스(universe·holdings 의 dict·문자열 날짜)만 맞춘다 — collectors.LegacyAdapter.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collectors import LegacyAdapter  # noqa: E402

from kbj.data.private.etf_issuers.koact import KoAct as _KKoAct  # noqa: E402


class KoAct(LegacyAdapter):
    KBJ = _KKoAct
