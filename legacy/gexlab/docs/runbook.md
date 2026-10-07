# 운영 런북

## 로컬 개발 (맥북)

```bash
uv sync                      # Python 3.12 + 의존성
uv run pre-commit install    # 커밋 전 ruff·pyright·pytest
cp .env.example .env         # 키 채우기 (git 에 올리지 않는다)
uv run pytest -m "not integration"   # 컨테이너 없이 (단위·속성)
uv run pytest -m integration         # 컨테이너·compose 시험 스택 (Docker 필요, 없으면 건너뜀)
```

- 마크 `integration` = compose·컨테이너가 필요한 시험(tests/integration). 옛 이름 `docker` 는 별칭이다
- 하루 통합 시험(`tests/integration/test_full_day.py`)은 docker-compose.yml + `tests/integration/docker-compose.test.yml`
  로 프로젝트 `gexlab-fd-<무작위>` 스택(redis·db, 127.0.0.1 임의 포트)을 띄우고 끝나면 `down -v` 로 지운다
  (restart "no" — 시험 프로세스가 강제 종료돼 남은 `gexlab-fd-*` 컨테이너는 다시 켜지지 않는다. `docker ps -a --filter name=gexlab-fd-` 로 찾아 `docker compose -p <프로젝트> down -v`)

## probe (Phase 0)

- 로컬: `uv run python -m scripts.probe_all [이름...]` 또는 `docker compose run --rm probe`
- CI: Actions → KIS·KRX probe → Run workflow. 필요한 secrets: `KIS_APP_KEY`, `KIS_APP_SECRET`, `KRX_API_KEY`
- 결과는 `docs/probe_results.md` 에 옮긴다
- 여러 번 돌릴 때는 `PROBE_OUT_DIR=probe_out/runs/<YYYYMMDD_HHMM>` 로 폴더를 나눠 결과가 서로 덮어쓰지 않게 한다

### 예약 측정 (crontab)

야간 전용 측정(#19 `night_board`)처럼 사람이 없는 시각에 돌려야 하는 probe 는 crontab 에 **한 번만** 걸고, 결과를 옮긴 뒤 줄을 지운다.

**2026-09-29 00:47 에 GEXLAB crontab 줄을 모두 지웠다(#19 실측 완료).** 21:07 줄은 맥이 잠자기여서 돌지 않았다 — 야간 측정은 00:42·00:44 수동 실행으로 대신했다. 아래는 기록용이다.

설치했던 줄 (2026-09-28):

```cron
# GEXLAB probe 예약 (2026-09-28 설치, 측정 끝나면 삭제 — docs/runbook.md)
7 21 28 9 * cd <GEXLAB 체크아웃 경로> && PROBE_OUT_DIR=probe_out/runs/$(date +\%Y\%m\%d_\%H\%M) <uv 절대경로> run python -m scripts.probe_all night_board >> probe_out/runs/cron.log 2>&1
```

- 18:05 첫 표본은 사용자 지시로 **직접 실행**한다(16:45 에 걸었던 18:05 cron 줄은 17:00 쯤 지웠다 — 수동 실행과 같은 분에 겹치면 probe 토큰 발급 1분 1회에 걸린다). 21:07 cron 은 둘째 표본

- 날짜·월을 고정(`28 9`)해 그날 한 번만 돈다. 지우지 않으면 **내년 같은 날 다시 돈다** — 측정이 끝나면 `crontab -l | grep -v 'GEXLAB probe' | grep -v 'probe_all' | crontab -` 로 지운다
- `%` 는 crontab 에서 줄바꿈이라 `\%` 로 적는다. `uv` 는 cron 의 PATH 에 없어서 절대경로로 적는다
- **맥이 잠자기 상태면 cron 은 돌지 않고, 깨어난 뒤에도 놓친 작업을 다시 돌리지 않는다.** 예약 시각에 맥이 깨어 있고 네트워크에 붙어 있어야 한다(덮개를 닫지 말 것, 필요하면 `caffeinate -s` 로 잠자기 방지)
- 설치 직후 2분 뒤 한 번만 도는 자가 점검 줄(`date >> probe_out/runs/cron.log`)로 cron 이 이 폴더에서 도는지 확인했다(2026-09-28 14:07 통과). 자가 점검 줄은 지웠다
- 결과: `probe_out/runs/<시각>/`, 실행 로그: `probe_out/runs/cron.log`(키·토큰은 probe 가 가린다)
- `night_board` 는 d43212e 부터 월물리스트의 위클리 전 시리즈 + 월물 1개를 재고, 시리즈마다 최종거래일(`last_tr_date`)과 만기 지남(`expired`)을 남긴다. 판정은 `night_board.json` 의 `series` 중 `last_tr_date` 가 있고 20260928 보다 늦은 시리즈(WKM·WKI 261001, 월물 202610)로만 한다(21:07 엔 09-28 15:20 만기인 WKM 260904 가 `expired`). `is_expired` 는 `last_tr_date` 가 비면 `false` 라서, 만기 뒤 전광판·단건이 빈 응답인 죽은 시리즈가 '거래 중'으로 남고 summary 의 '대표' 로 뽑힐 수 있다 — `last_tr_date` 가 빈 시리즈는 만기 지남·불명으로 보고 제외한다
- 예약을 놓쳤으면 다음 야간(18:00~06:00)에 같은 명령을 직접 실행한다

## 수집 스택 (Phase 1, docker compose)

```bash
cp .env.example .env   # KIS_APP_KEY·KIS_APP_SECRET·POSTGRES_PASSWORD·DATABASE_URL 채우기
docker compose up -d --build   # redis·db → migrate(한 번) → auth·scheduler·recorder·poller·ws-gateway
docker compose ps              # 서비스마다 healthy (하트비트 healthcheck)
docker compose logs -f poller  # JSON 로그 (service·trade_date·session·event)
```

- `DATABASE_URL` 은 compose 안 호스트 이름 `db` 로: `postgresql://gexlab:<POSTGRES_PASSWORD>@db:5432/gexlab`. 비밀번호는 URL 에 그대로 넣을 수 있는 영숫자로(`openssl rand -hex 24`)
- 공개 포트가 없다. DB 를 들여다볼 땐 `docker compose exec db psql -U gexlab -d gexlab`
- Redis 계약(채널·키)은 `services/bus.py` 한 곳. 세션 상태: `docker compose exec redis redis-cli GET session:state`, 하트비트: `... GET health:heartbeat:<서비스>`
- DB 가 멈추면 서비스는 쓰기 묶음을 볼륨 `spool`(컨테이너 `/app/state/spool/<서비스>/`)에 쌓고, DB 가 돌아오면 순서대로 재적재한다. health_events 에 `db_spooling`·`db_spool_replayed`(버리면 `spool_dropped`, 재적재 안 되는 묶음은 `spool_dead_letter` + `_dead/`) — 상한은 서비스마다 `SPOOL_MAX_MB`(기본 1024)
- 멈출 땐 `docker compose stop` (SIGTERM → 묶음·스풀을 비우고 끝낸다, 30초 여유)

## engine·기능 플래그·검증 리포트 (Phase 3)

```bash
# 플래그: config/features.yaml 을 제자리에서 고친다(engine 은 30초 안에 다시 읽는다 — 읽기 전용 마운트라
# 파일을 새로 만드는 편집기는 컨테이너가 못 본다: cp 새파일 config/features.yaml 또는 engine 재기동)
docker compose logs engine | grep -E 'flags_(loaded|reloaded|error)'
# 섀도 운영 점검 — 최근 5거래일, 종료 코드 0 이면 '1주 섀도 무오류' 충족 (DATABASE_URL 필요)
uv run python -m scripts.shadow_report [--end 2026-10-08] [--days 5] [--flag shadow]
# 지표별 검증 리포트 다시 만들기 (probe_out 의 2026-09-28 체인 스냅샷 — 없으면 git 발췌로)
uv run python -m scripts.metric_report --probe-dir probe_out --write
```

- 플래그 값: `off`(계산 안 함) · `shadow`(계산·저장만 — 발행·알림 안 함) · `visible`. 모르는 이름·값이면 engine 이 기동하지 않고(종료 코드 2), 돌던 중 틀리게 고치면 직전 플래그로 계속 돌며 health `engine_flags_invalid`
- 새 지표를 visible 로 올리기 전: `docs/metric_validation.md` 교차 확인 일치 + 1주 섀도 점검 충족

## 복구 절차

(Phase 5 에서 채운다: DB 복구, 토큰 만료, 웹소켓 강제 종료)
