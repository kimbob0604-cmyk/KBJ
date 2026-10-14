# ADR 0028 — 패시브 지수 정기변경(편입·편출) 예측: 지수별 규칙 모듈·컷오프 추정·로그인 전용 화면 (2026-10-13)

상태: **초안**(P5 2차 묶음 IDX — 메인이 커밋 때 상태 확정). 번호: 0025~0027 은 `docs/p6_design.md` 가 미리 잡아 두어
(0025 공개 예산·0026 FLASH·0027 MAP) 그 다음 번호를 쓴다(0020 은 THM 자리).
근거: `docs/p5_index_rebalance_prompt.md`(지시·검증 근거 §1), `docs/metrics.md` §24, `docs/DATA_TIERS.md`(지수 구성종목·예측 =
로그인), ADR 0001 U4(네이버 스크래핑 금지), ADR 0018(장 마감 뒤 발송 16:00 — 이 묶음은 발송 없음), ADR 0024(페이지 10 P5 패널).
구현: 백엔드 `config/index_reviews.yaml`·`kbj/config/index_reviews.py`·`kbj/engines/index_review/**`·`kbj/store/migrations/
0018_index.sql`·`kbj/store/repos/index_review.py`·`kbj/services/engine/index_review.py`·API(`models|readers|routes/index_review.py`),
화면 `web/src/pages/p10/index_review.ts`(페이지 10)·`web/src/pages/p5/index_events.ts`(페이지 5 캘린더 줄)·
`web/src/pages/p8/index_badges.ts`(페이지 8 머리 배지).

## 1. 맥락

- 사용자가 MSCI·KOSPI200·KOSDAQ150 정기변경 편입·편출 후보와 컷오프 대비 괴리율·필요 주가 상승률·예상 패시브 매수를
  표로 보고 싶어 했다(사용자 예시 열). 기대는 데이터(P3 KRX 일별·ETF 순자산, P4 DART 지분)는 이미 있다.
- MSCI 는 컷오프를 공표하지 않고, 방법론 숫자(배수·완충·반올림)는 판마다 바뀐다(유동비율 새 반올림은 2026-05 리뷰부터).
  KRX 방법론은 업종별 누적 시총 비중 등 원문 대조가 필요하다. 지수 구성종목 목록은 지수 운용사 독점 자료다.

## 2. 결정

1. **지수마다 규칙 모듈 하나**(순수 함수·시계·설정 주입): `msci.py`(컷오프 배수 형)·`krx.py`(시총 순위·완충 형), 공통은
   `fif`(유동비율 반올림·완충·FOL)·`cutoff`(MSCI 커버리지 방식 추정)·`required`(필요 상승률 = max(시총·유동 조건) 1bp 올림)·
   `passive`(추종 자금 × 비중). FTSE·KRX300 은 같은 틀에 설정만 더한다(`enabled: false`).
2. **숫자는 설정 한 곳**(`config/index_reviews.yaml` — 값마다 src·checked·verify). 확인 전 값은 `verify: true` = [확인 필요],
   일정은 사람이 적고(`confirmed: false` 면 '추정'), 지어내지 않는다: 추종 자금이 없으면 패시브 규모를 비우고, 구성종목
   목록이 없으면 `no_constituents` 로 돌고 후보를 내지 않는다.
3. **컷오프 두 벌을 나란히**: 엔진 추정(구간·방법 — `estimated`)과 사용자 입력(증권사 추정치 — `set-cutoff`). 같은 표 모양으로
   둘 다 계산해 화면에 한 줄씩 비교하고, 후보 표도 컷오프마다 그린다.
4. **화면은 기존 양식 안에서만**(새 페이지·새 색·새 글꼴 없음): 페이지 10 패널 '지수 정기변경' 하나(지수 탭 = `seg`,
   리뷰 고르기 = `select`, 일정 띠 = `kv` + D-day, 표 = `table`, '추정' = `estBadge`), 페이지 5 경제 캘린더에 리뷰마다
   '결과 발표'·'리밸런싱(패시브 매매)' 두 줄(같은 표에 날짜순으로 섞음 — 실패해도 캘린더는 계속, notes 한 줄),
   페이지 8 머리에 배지 한 줄(서버가 만든 label 그대로 — 유지 종목은 배지 없음).
5. **로그인 전용**: API(`/api/index-review*`)·구성종목·컷오프 추정은 공개 빌드·공개 내보내기에 없다. 웹은 P5 와 같은
   `import.meta.env.MODE === 'public' ? null : …` 방식으로 세 모듈을 공개 번들에서 빼고, 공개판 페이지 10 은 같은 제목의
   자물쇠 패널만 그린다(요청 0). `scripts/check-bundle.mjs` 공개 금지 문자열에 `index-review` 를 더했다.
6. **텔레그램 없음**(발표일 알림 등은 P8 규칙 알림 — ADR 0018 16:00 규칙 유지). 작업 `index.calendar`·`index.predict`
   (17:20 KST, 16:00 발송 묶음 뒤)는 `enabled: false` 로 등록 — 구성종목 목록·일정 확인 뒤 메인이 켠다.

## 3. 결과

- 합성 세계(고정 시드 240종목)에서 세 지수 리뷰 하나씩 후보가 계산되고(골든), 반올림·배수·완충·필요 상승률 역산 속성
  시험이 녹색이다. 웹 시험 `web/test/pages/p10_index_review.test.ts`(지수 탭·D-day 띠·빈 목록 상태·'추정'·필요 상승률 정렬·
  사용자 컷오프 나란히·페이지 5 줄·페이지 8 배지·공개 자물쇠), 웹 픽스처 `web/test/fixtures/api/index_review/**`
  (`tests/unit/api/web_fixtures_p5/index_review.py` — P5 도메인 목록에 `index_review` 를 더했다).
- 공개 JS gzip 은 61,021 B → 61,217 B(+196 B — 자물쇠 패널·캘린더 고리; 예산 61,440 B 안). P6 는 예산을 72 KB 로 올릴
  예정(ADR 0025 초안).
- 웹 API 경로 형식(`web/src/data/api.ts` `API_PATH`)이 첫 마디 하이픈(`index-review`)을 받도록 넓혔다(낱말 사이 하이픈만 —
  `..`·절대 URL·쿼리 섞기는 그대로 금지).

## 4. 개정 — 공개 출처로 규칙 값 확인(2026-10-14)

MSCI 규칙을 GIMI 방법론 2026-08판 원문(§2.3.1·§3.1.5·§3.1.6·§3.1.7·§3.1.2.6·§3.1.6.3·App.VI·App.VIII)과 MSCI 공지
(2026-01-27 유동비율 과도기·2026-08-12 리뷰 일정표)로 맞췄다. 정기 리뷰 편입 배수는 비구성종목 1.0×·하위 세그먼트(Small
Cap) 이동 1.5×·유동 0.5×(옛 1.8×·0.9× 는 Light Rebalancing 전용 — `light_rebalance`, 기본 꺼짐), FIF < 15% 는 유동
0.9×·0.6×. 설정 키를 `add_mcap_mult_new`·`add_mcap_mult_migrate`·`low_fif_ff_mult_add|keep`·`segment_default` 로
나눴다(비구성종목의 Small Cap 구성 여부를 모르면 보수적으로 1.5×). FIF 완충은 '≤' → '<'(단위 이상 변화는 반영)로 고치고
신규 편입·세그먼트 이동·FOL 로 인한 변경은 완충 없이 반영한다. FOL 은 0.1% 반올림 + foreign room 계수(신규 25%·15%
문턱·0.5, 기존 3.75% 미만 0.25 — 중간 단계 표는 원문 이미지라 [확인 필요]). MSCI 2026 일정 네 건은 KST 발표일로 확인
(confirmed). KRX 는 원문을 받지 못해 2차 자료(삼성증권 2026-03-27·서울경제 2019-12-12)로 순위 비율을 '산업군 안 순위 ÷
산업군 구성종목 수'로 바꾸고 산업군 누적 시총 비중(KOSPI200 85%·KOSDAQ150 60%)·심사기간 6개월 평균 시총·KOSDAQ150
0.8/1.2 를 옮겼다(verify: true 유지). 2026-11-02 KRX 개정(KRICS 9개 산업군·예비종목 폐지)의 산업군 매핑은 2026-10-26
공개 자료를 받은 뒤 한다. 합성 골든은 이 규칙으로 다시 만들었다(허용오차 그대로 — 바뀐 이유는 시험 머리말).

## 5. 남은 것

- [확인 필요] KRX 방법론 원문·2026-11-02 개정판 수치·KRICS 산업군 매핑, FOL 기존 종목 중간 단계 계수, 추종 자금·목록
  형식·KRX 2026-12 발표일 — `docs/probe_results.md` #50~#54.
- 사용자 컷오프 입력은 CLI(`set-cutoff`)만 — 로그인 화면 입력은 POST 라우트가 필요(다음 묶음).
- 작업 켜기(`P5_ENABLED`·하루 운영 시뮬레이션 resources)와 Docker 통합 시험(0018) 확인은 메인.
