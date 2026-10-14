"""KIS 웹소켓 호스트 — KBJ 에서 이 주소를 갖는 유일한 곳(docs/p7_design.md D-P7-10).

`check_canonical` 그룹 `kis_ws` 의 허용 위치는 `kbj/services/ws_gateway/*` 뿐이고, 그 안에서도 주소
문자열은 이 파일에만 둔다. GX 는 `config/kis_ws.yaml` 의 `url.real`·`url.vts` 에 두었다 — P7 에
설정을 `config/` 로 옮기며 호스트 줄을 뺐다(설정은 튜닝값만, 주소는 코드 상수).

값은 KIS 공식 샘플 저장소 koreainvestment/open-trading-api `kis_devlp.yaml`(커밋은
`config/kis_ws.yaml` 의 `source.commit`)의 `ops`·`vops` 그대로다. 평문 ws — 접속키가 평문으로 간다
(KIS 방식). 모의(vts)는 시세 수집에 쓰지 않는다(PLAN §4.2).
"""

from __future__ import annotations

from typing import Final, Literal

Env = Literal["real", "vts"]

REAL_URL: Final = "ws://ops.koreainvestment.com:21000"  # 공식 ops
VTS_URL: Final = "ws://ops.koreainvestment.com:31000"  # 공식 vops(모의)


def url_for(env: Env) -> str:
    """환경 → 웹소켓 주소."""
    return REAL_URL if env == "real" else VTS_URL


__all__ = ["REAL_URL", "VTS_URL", "Env", "url_for"]
