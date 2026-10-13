# DART 합성 원문 fixture (공개 등급 — 실측 아님)

DART 는 공개 등급이라 실응답 fixture 도 커밋할 수 있지만(`docs/DATA_TIERS.md` §1) 키를 받기 전이라 공시
원문 **모양**을 합성으로 만든다(`docs/p4_design.md` §1.3·§8.2 — 묶음 F). 실데이터 fixture 는 키를 받은 뒤
`tests/fixtures/public/dart/` 에 둔다(보고자 이름은 합성으로 바꿔 넣는다 [제안]).

| 파일 | 내용 | 쓰는 시험 |
|---|---|---|
| `p4_docs.json` | 공시 원문 HTML 20개 — 합성 20종목 세계(`../p4_world.py`, 시드 20261007)의 원문 15개(잠정실적 6열 억원·6열 백만원(기간 표기 없음)·2열·별도 천원·자회사·정정, CB·BW·유상증자 결정, 전환청구·신주인수권 행사, 전환가액 조정, 결산실적공시예고, 소유보고, 일반 안내) + 세계에 없는 폼 5개(단위 '원'·괄호·△ 음수, 단위 표기 없음, 연간 기간, 회차 둘인 조정·청구) | `tests/unit/data/public/test_dart_{prelim,overhang_doc,ownership,fixtures}.py` |

- 생성기: `make_dart.py`(`uv run python tests/fixtures/synthetic/dart/make_dart.py --write`). 커밋본이 생성기
  출력과 같은지는 `tests/unit/data/public/test_dart_fixtures.py` 가 본다. 세계(`p4_world.py` — 묶음 M)가 바뀌면
  다시 만든다.
- 머리 칸 `_source` 가 `SYNTHETIC` 으로 시작하고, 세계에서 온 원문 HTML 에는 `<!-- SYNTHETIC … -->` 주석이 있다.
- 표 모양(잠정실적 열 순서, 결정 공시 문단, 행사·조정 표 머리, 결산실적공시예고 표, 소유보고 변동사유 칸)은
  **[확인 필요 #30~#32]** 추정이다. 실측으로 바뀌면 파서 모듈의 머리 이름 상수와 이 생성기를 같이 고친다.
