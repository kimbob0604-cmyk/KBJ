"""DART OpenAPI 클라이언트 — KBJ P2: KBJ 정본을 다시 내보낸다(설계 §1.4·§9.4, 두 벌 금지).

정본은 `kbj.data.public.dart.client.DartClient`(이 파일에서 승격 — ETF-Traker `dart-report/
dartreport/client.py`). 여기에는 옛 생성자 모양만 남긴다(run.py:58 `DartClient(cache_dir=…)`,
app.py:69 `DartClient(api_key=…)`).

옛 것과 달라진 점(MIGRATION.md P2)
- 키: 인자로 주지 않으면 KBJ 설정 `KBJ_DART_API_KEY` 를 읽는다(옛 환경변수 DART_API_KEY 는 읽지
  않는다). 없으면 `SystemExit` 대신 `DartError`(DartKeyMissing).
- 스로틀: `throttle` 은 받기만 한다 — 공용 리미터(config/limits.yaml `dart` 8/s)가 대신한다.
- `DartError.status` 는 HTTP 상태이고 DART 상태 코드는 `.code` 다(이 패키지는 `.status` 를 읽지 않는다).
- `disclosures` 는 `max_pages`(20쪽)를 넘으면 조용히 자르지 않고 `DartError` 를 낸다.
- 캐시(`.cache/`)는 그대로 — 캐시 키·내용에 `crtfc_key` 가 없다. 공시 목록은 캐시하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

from kbj.data.public.dart.client import EMPTY_STATUSES, REPRT, DartError
from kbj.data.public.dart.client import DartClient as _KbjDartClient

__all__ = ["EMPTY_STATUSES", "REPRT", "DartClient", "DartError"]


class DartClient(_KbjDartClient):
    """옛 생성자 호환 — `DartClient(api_key=None, cache_dir=Path('.cache'), throttle=0.12,
    timeout=30)`. 메서드(`get_json`·`get_zip`·`corp_code`·`financials`·`disclosures`·
    `document_texts`)는 KBJ 정본 그대로다."""

    def __init__(
        self,
        api_key: str | None = None,
        cache_dir: Path | str = Path(".cache"),
        throttle: float = 0.12,
        timeout: int = 30,
    ) -> None:
        del throttle  # 공용 리미터가 대신한다
        super().__init__(api_key, cache_dir=Path(cache_dir), timeout=float(timeout))
