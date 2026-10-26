# ADR 0042 — 공개 투자 콜 기록(`calls/`): 파일 하나 = 콜 하나·판단 칸 고치기 금지·CI 비교 검사 (2026-10-26)

상태: **채택**. 근거: 사용자 요청(취업 포트폴리오 — "내가 언제 무엇을 판단했고 결과가 어땠는지"를 보여 줄 기록),
DATA_TIERS(공개/로그인 등급), 사용자 결정(밸류에이션·업사이드는 주가가 아니라 시총 기준).
구현: `calls/`(README·TEMPLATE), `kbj/engines/journal/calls.py`(읽기·검사·성과 — 순수), `kbj/services/personal/calls.py`
(CLI `calls check|list`), CI `calls` 잡, 지표 `docs/metrics.md` §32, 시험 `tests/unit/engines/journal/test_calls.py`·
`tests/unit/services/test_personal_calls.py`.

## 1. 맥락

- 분석 일지(`prv_journal.analysis` — P8 JNL, ADR 0033)는 로그인 DB 안에 있다. 내가 언제 썼는지를 남이 확인할 수 없고,
  고쳐도 흔적이 없다. 포트폴리오로 쓰려면 **제3자가 확인할 수 있는 시점 기록**이 필요하다.
- KBJ 레포는 공개다. 커밋 시각·이력은 GitHub 가 보여 주므로, 콜을 파일로 커밋하면 별도 서비스 없이 시점 기록이 된다.
  단, 나중에 고치면 의미가 없어서 고치지 못하게 막는 장치가 같이 있어야 한다.

## 2. 결정

1. **파일 하나 = 콜 하나**: `calls/<YYYY>/<YYYY-MM-DD>-<코드>-<소문자-슬러그>.md`. 머리(YAML)에 시장·코드·방향·기간·
   진입/목표 시총(억, 시장 통화)·진입 시총 기준일과 출처·벤치마크·킬 조건(1개 이상)·상태, 본문에 `## 논리`(필수)·
   `## 밸류에이션`·`## 갱신`. 모르는 칸은 오타로 보고 거절한다.
2. **판단 칸은 고치지 않는다**: `id·opened·market·code·direction·horizon_months·entry_mcap_eok·entry_mcap_as_of·
   target_mcap_eok·benchmark·kill_criteria` 와 `## 갱신` 앞 본문(줄 끝 공백·빈 줄 차이는 무시). 바뀐 생각은 `## 갱신`
   아래에 날짜와 함께 덧붙이거나, 콜을 닫고 새 콜을 낸다. 닫기(`status: closed` + `closed·close_mcap_eok·close_reason`)는
   한 번만 — 닫힌 콜은 다시 열지 않고 종료 칸도 고치지 않는다. **콜 파일은 지우지 않는다**(틀린 콜도 기록이다).
3. **소급 기록 막기**: 기준 버전에 없던 새 콜의 `opened` 가 검사일(KST)보다 `MAX_BACKDATE_DAYS`(3일) 넘게 이르면 거절.
   [확인 필요] 3일은 주말·연휴에 늦게 올리는 경우를 본 기본값이다.
4. **검사는 CI 에서 기준 버전과 비교**: `calls check --against <기준>` — PR 은 base 브랜치, push 는 직전 커밋(새 브랜치면
   `origin/main`). 이력이 필요해 잡만 `fetch-depth: 0`. 형식 검사는 `test-kbj` 의 `test_repository_calls_are_valid` 도 본다.
   이것은 **강제력이 아니라 탐지**다 — 강제 푸시로 이력을 바꾸는 것까지 막지는 못한다(브랜치 보호는 사용자 설정 몫).
5. **등급**: 콜 파일(내가 쓴 판단·진입/목표/종료 시총 한 값씩)은 공개. 시세로 매일 계산하는 성과 시계열은 원천(KRX·KIS)이
   로그인 등급이라 레포·공개 사이트에 넣지 않는다(DATA_TIERS §1 — 행 추가). 시험 콜은 전부 합성 값.
6. **성과는 시총 기준**(사용자 결정): 콜 수익률 = 평가/진입 − 1(숏은 부호 반대), 상대 성과는 벤치마크 대비(metrics §32).
   증자·CB 전환으로 주식 수가 늘면 시총 수익률이 주주 수익률보다 좋게 나온다 — 화면·문서에 그 한계를 적는다.
7. **DB 를 쓰지 않는다**: CLI 는 `personal` 그룹 모양(`cli(argv, repos)`)만 따르고 저장소를 열지 않는다.

## 3. 결과·남은 것

- 콜을 커밋하면 GitHub 이력이 시점 증거가 되고, CI 가 고친 흔적·삭제·소급을 잡는다.
- [남은 것] 로그인판 성과 표: VM 에서 열린 콜의 평가 시총(KRX·KIS — 로그인)을 붙여 `call_mark` 로 계산해 페이지 12 에
  보이기, 닫힌 콜의 공개 요약(종료 시총 기준 — 공개 가능)을 README 표로 만드는 작업. 둘 다 이번 범위 밖.
- [남은 것] `main` 브랜치 보호(강제 푸시 금지)는 레포 설정에서 사용자가 켠다.
