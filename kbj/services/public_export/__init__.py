"""공개 정적 사이트 내보내기 — `pub_*`·코드 계산(캘린더·금통위)만 JSON 으로(p3_design §7).

- 등록부 작업 `public.export`(05:30, always) 의 처리기는 `run`(`export.py`).
- 로컬 빌드: `python -m kbj.services.public_export build --out <dir>`, 검사: `... check <dir>`.
- 공개 사이트(Pages)는 이 산출물을 `public-data` 브랜치로 받아 SPA 공개 빌드와 합친다
  (`.github/workflows/pages.yml` — 수동 실행만, `scripts/build_public_site.sh`).
- 섞이지 않게 하는 겹(§7.4): DB 역할 `kbj_public_export`(pub_* SELECT 만), import 계약 ⑧(로그인
  등급 코드 import 금지), SQL 정적 시험(pub_ 만), 내용 검사(`manifest.check_payload`), 번들 검사.
"""

from kbj.services.public_export.export import export_all, run
from kbj.services.public_export.manifest import Manifest, check_tree
from kbj.services.public_export.push import PushResult, push_public_data

__all__ = ["Manifest", "PushResult", "check_tree", "export_all", "push_public_data", "run"]
