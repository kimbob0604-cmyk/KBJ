"""stock-dashboard 테마 마스터 어댑터 — 로그인 쪽(네이버 유래 파일 묶음, ADR 0039).

받기는 공개 쪽 클라이언트(`kbj.data.public.stock_dashboard.client`)를 그대로 쓴다(ECOS 방식 — 로그인
→ 공개 import 는 계약 ① 이 허용). 이 패키지는 로그인 데이터셋(`STOCK_DASHBOARD:theme_master`)만
둔다.
"""

from __future__ import annotations

from kbj.data.private.stock_dashboard.datasets import DATASETS

__all__ = ["DATASETS"]
