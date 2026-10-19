---
name: local-verify
description: 맥에서 도는 KBJ(로컬 도커)를 직접 돌려 보고 틀리거나 비거나 멈춘 화면 데이터를 찾아 고칠 때 쓴다 — "직접 돌려보고 고쳐", "데이터 안 맞아", "업데이트 안 돼", "점검해" 요청.
---

# 로컬 실데이터 점검·수정 (맥, `scripts/local/kbj.sh`)

사용자는 개발자가 아니다. 사람이 화면을 하나씩 확인하지 않아도 되게, 이 세션이 직접 돌리고 확인하고 고친다.

## 지킬 것
- `.env`·토큰 캐시를 열지 않는다(설정에서 막혀 있다). 키 값은 어디에도 찍지 않는다. 키가 없으면 `kbj.sh keys` 를 사용자에게 부탁한다.
- 실데이터 응답·화면 값을 커밋하지 않는다(공개 레포 — CLAUDE.md §2). 시험은 합성 데이터로.
- `LIVE_TRADING=false` 그대로. 주문 코드 없음.
- 데이터 볼륨을 지우는 명령(`docker volume rm`, `stop --down -v` 등)은 쓰지 않는다.
- 작업 가지는 `claude/build`. 고친 뒤 ruff·pyright·관련 pytest(결과는 파일로 받고 `EXIT=` 확인)·웹이면 `npm --prefix web run test`/`lint`/`typecheck` 를 통과시키고 푸시.

## 순서
1. 최신 코드로 띄우기: `git pull` → `bash scripts/local/kbj.sh update`(백업 후 다시 빌드·기동). 첫 빌드는 오래 걸린다.
2. 상태: `bash scripts/local/kbj.sh status` — 컨테이너·접속·운영 점검(작업 실패·하트비트 끊김). 실패 작업은
   `bash scripts/local/kbj.sh status --logs scheduler`(또는 `api`·`auth`·`gex-poller`)로 원인을 본다.
3. 화면 데이터 전체: `bash scripts/local/kbj.sh audit` — 화면 API 82개의 상태·기준 시각(as_of)·나이·품질·빈 칸.
   - 404 는 사유에 채울 작업 이름이 나온다 → `kbj.sh job <작업>` 으로 한 번 돌리고 다시 audit.
   - 200 인데 나이가 마지막 거래일보다 오래됐으면(예: 신고가 보드) 그 작업의 일정·실행 기록·로그를 본다.
   - 5xx 는 코드 버그 — `status --logs api` 로 추적.
4. 값 대조: `bash scripts/local/kbj.sh audit --show /api/market/summary` 처럼 한 응답을 보고, 공식 공개 출처(KRX 정보데이터시스템,
   한국은행 ECOS, DART, 거래소 공시)의 같은 날 값과 맞춰 본다. 다르면 원인(단위·코드·날짜·부호)을 코드에서 찾는다.
5. 고치기 → 시험 → 커밋·푸시 → `kbj.sh update` → 같은 audit/대조로 고쳐졌는지 확인. 확인 못 한 것은 "확인 못 함"이라고 적는다.
6. 사용자에게는 짧게: 무엇이 틀렸고, 무엇을 고쳤고, 무엇이 남았는지(장중에만 확인되는 것 포함).

## 시간대 메모
- 장중(09:00~15:30)에만 확인되는 것: 장중 수급(flows.market_minute), GEX, 프로그램 매매, 장중 시세.
- 장 마감 뒤: 마감 수집(market.close_collect)·신고가 보드(board)·투자자별 일별.
- 맥이 잠들면 수집이 빈다(분 단위 수급은 나중에 못 채움). `kbj.sh auto status` 로 확인.
