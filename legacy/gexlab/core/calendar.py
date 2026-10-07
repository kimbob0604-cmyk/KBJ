"""거래 캘린더 — KBJ P2: 정본은 `kbj.core.calendar`(이 파일에서 승격, 설계 §1.3·§7.2).

legacy GX 는 이름을 그대로 쓰도록 공개 이름 전부를 다시 내보낸다(두 벌 금지 — CLAUDE.md §3).
휴장 덮어쓰기 파일의 정본은 레포 루트 `config/holidays_override.yaml` 이다(이 폴더의
`config/holidays_override.yaml` 사본은 읽히지 않는다). 시험이 기본 캘린더를 바꿔 끼울 때는
`kbj.core.calendar._default_calendar` 를 패치한다(이 모듈은 비공개 이름을 내보내지 않는다).

승격한 시험: `tests/unit/test_calendar.py`(137)·`tests/property/test_calendar_properties.py`(7)·
`tests/unit/test_dependencies.py` 의 XKRX 시험(7) → kbj `tests/unit/core/`·`tests/property/`.
"""

from kbj.core.calendar import *  # noqa: F403
