# ADR 0039 — 테마 마스터 받아 쓰기(페이지 9 테마·밸류체인) (2026-10-15)

상태: **확정**(사용자 결정 2026-10-15). 근거: CLAUDE.md §2(네이버 스크래핑 금지), docs/DATA_TIERS.md §1·§2 9 행,
ADR 0002(등급은 스키마로), ADR 0025(공개 JS 예산), docs/metrics.md §31. 이 ADR 이 PLAN P5 의 '분류 사전 통합(페이지 9)'
제외(2026-10-13 Q1)를 마감한다.

## 1. 맥락

페이지 9 는 P5 에서 '사용자가 따로 진행'으로 빠져 자리표시였다. 사용자는 stock-dashboard 레포(공개)에서 대테마 44개·섹션·
밸류체인 구조를 직접 만들고, 매주 GitHub Actions('종목 마스터 갱신' — 일 06:00 KST, 바뀌면 PR)로 `data/master/`
(themes.json·naver_themes.json·naver_theme_members.csv·stocks.csv·industry_groups.json·meta.json·type_overrides.csv)를
만든다. 이 중 네이버 테마 이름·편입 종목·편입 사유·업종은 원천의 원천이 네이버다.

## 2. 결정

1. **KBJ 는 네이버에 접속하지 않는다.** stock-dashboard 가 만든 raw 파일만 받는다(주소 `config/theme.yaml master.base_url`
   — 기본 GitHub raw main, 바꿀 수 있다). 직접 호출 검사(`check_canonical` naver 그룹)는 그대로 0.
2. **등급** — 대테마 분류(id·이름·섹션·밸류체인, 사용자 작성)는 **공개**, 네이버 유래(네이버 테마 이름·설명·편입 종목·
   사유·종목 마스터)와 시세 기반 강도는 **로그인**. 표로 나눈다: `pub_themes.theme` 대 `prv_themes.*`(0023).
   원천 표기: 공개 `stock-dashboard:data/master`, 로그인 `stock-dashboard:data/master+naver(stock-dashboard 수집)`,
   as_of = `meta.json built_at`.
3. **작업 둘(주 1회 — 일 12:40 KST, 휴장 무관)** — 등록부 규칙(로그인 원천을 받는 작업은 공개 표에 쓰지 않는다)을
   지키려고 데이터셋·작업을 등급마다 하나씩 둔다.
   - `theme.master_sync`(로그인, `STOCK_DASHBOARD:theme_master`): 파일 일곱 → 검증 → `prv_themes.*` 한 트랜잭션 교체.
   - `theme.classes_sync`(공개, `STOCK_DASHBOARD:theme_classes`, 굳은 의존 → master_sync): meta·themes.json 의 사용자 작성
     칸만 → `pub_themes.theme` 한 트랜잭션 교체 → 공개 내보내기 `themes.json`.
   - 둘 다 `built_at` 이 직전 적재와 같으면 meta.json 만 받고 끝(ok, rows 0), **더 오래된 판이면 실패**(사람이 확인).
4. **검증(거부 = 적재하지 않음·옛 판 유지·재시도 뒤 실패 알림)** — 필수 칸·JSON/CSV 모양, id·번호·코드 형식과 중복,
   참조(편입의 테마 번호 ⊂ 네이버 테마, 종목 ⊂ 종목 마스터, meta.total = 종목 수), 개수 하한(대테마 ≥ 30·네이버 테마
   ≥ 150·편입 ≥ 3,000·종목 ≥ 2,000)과 직전 판 대비 감소율 20% 초과 거부(`config/theme.yaml` — [제안] 숫자). 새 네이버
   테마·없어진 번호·이름 바뀜은 경고만(적재 기록 `warnings`).
5. **테마 강도는 저장하지 않는다** — 로그인 API 가 읽을 때 보드 종목 하루(`prv_board.stock_day` — KRX/KIS 정본 일봉에서
   보드가 계산한 등락·5일·21거래일 수익률·시총·신고가 라벨)로 계산한다. 집계는 보드 테마 집계와 같은 롤업(시총 가중 평균,
   `kbj.engines.board.aggregate.rollup` 재사용 — 두 벌 금지). 옛 계획 작업 `themes.monitor`(prv_themes.strength)는
   등록부에서 뺐다(2026-10-19 — 흡수하던 ET `kr.yml:build` 는 retired). 시세 날은 오늘 이하에서 종목 하루가 있는
   가장 최근 보드 날(오늘 보드 전·주말이면 지난 거래일 — 응답 `trade_date`·화면 '시세 … 마감').
6. **화면** — 페이지 9 mixed: 공개판은 대테마 분류·밸류체인(공개 파일), 로그인 패널 넷(강도 순위·히트맵·밸류체인 강도·
   편입 종목)은 자물쇠·요청 0. 로그인 본문(`p9/strength.ts`)은 공개 번들에서 뺀다(번들 검사 needle '편입 사유').
7. **공개 레포** — 실제 stock-dashboard 마스터 내용은 넣지 않는다. 시험·웹 픽스처·데모는 고정 시드 합성
   (`tests/fixtures/synthetic/theme_master.py`).

## 3. 결과

- 경계: import-linter 계약 ㉑(공개 분류 쪽은 로그인 원천·테마 로그인 모듈·로그인 저장소를 모른다), 공개 작업 SQL 시험
  (`test_public_writers` P9_PUBLIC_JOBS), 공개 파일 내용 검사(`manifest.theme_problems` — naver·members·reason·code 키 0).
- API: `/api/themes`(분류 — 공개 파일과 같은 모양)·`/api/themes/strength?period=1|5|20`·`/api/themes/{id}`.
- 남은 것: ① PR 머지가 늦으면 다음 주 반영(바로는 `run-once theme.master_sync` → `theme.classes_sync` 를 **같은 날(KST)
  차례로** — 굳은 의존이라 그날 master 성공 기록이 없으면 classes 는 'waiting' 만 찍고 돌지 않는다; 로컬
  `kbj.sh job` 은 이때 실패로 알린다) ② 문턱 숫자 실측
  [확인 필요] ③ 원천 파일 칸이 바뀌면 검증이 실패로 알린다(조용히 넘기지 않는다).
