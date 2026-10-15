# ADR 0040 — 운영 VM 배포: 오라클 무료 ARM VM, Caddy + sslip.io TLS 오버레이, 볼륨 밖 백업, 스크립트 업데이트 (2026-10-15)

상태: **제안**(사용자가 VM 에서 첫 기동을 확인하면 확정). 근거: docs/runbook.md ①·③·⑤·⑧, docs/p9_design.md §5·§6,
ADR 0008(로그인 API·TLS), ADR 0002 §2.2(공개 내보내기 역할), ADR 0037(운영 점검). 보충: **0008 §TLS** — '레포 밖 TLS
역방향 프록시' 를 '레포 안 **선택** 오버레이' 로 바꾼다(기본 compose 는 그대로).
구현: `scripts/vm/*.sh`, `docker-compose.vm.yml`, `deploy/Caddyfile`, 안내서 `docs/deploy_oracle.md`, 시험 `tests/test_vm_kit.py`.

## 1. 맥락

- P9 까지 KBJ 는 로컬·CI 에서만 돌았다. 런북 ①·③ 은 "서울 VM(사양 사용자 결정)", "TLS 프록시는 레포 밖 [사용자]" 로
  남겨 두었다. 사용자는 비개발자라 VM·도커·TLS·방화벽을 손으로 맞추기 어렵다.
- 비용 0 을 원한다. 오라클 클라우드 Always Free 의 Ampere A1(ARM64) VM 은 4 OCPU·24GB 까지 무료이고 서울·춘천 리전이
  있다. KBJ 이미지·의존성이 ARM64 에서 도는지 확인되지 않았다(probe #74 'arm64 미확인').
- api 는 호스트 네트워크 127.0.0.1:8000 에만 열린다(ADR 0008 — 로그인 잠금이 실제 IP 로 돌게). 바깥에서 들어오려면
  같은 호스트의 TLS 프록시가 필요하다. 텔레그램 웹훅(`POST /telegram/webhook`)도 같은 호스트·HTTPS 여야 한다.

## 2. 결정

1. **대상 VM**: 오라클 Always Free Ampere A1(VM.Standard.A1.Flex) 4 OCPU·24GB, Ubuntu 24.04(aarch64), 홈 리전 서울
   또는 춘천. x86_64 VM 에서도 같은 스크립트가 돈다(Docker 저장소 아키텍처를 `dpkg --print-architecture` 로).
2. **설치 한 줄**: `scripts/vm/install.sh`(curl | bash 또는 clone 뒤 실행 — 멱등). Docker 공식 저장소·compose 플러그인,
   시간대 Asia/Seoul(호스트만 — 컨테이너는 UTC 그대로)·NTP, 메모리 8GB 미만이면 스왑 4GB, 80·443 열기(오라클 Ubuntu 의
   iptables 기본 REJECT 앞에 — 지금 규칙과 `/etc/iptables/rules.v4` 둘 다. `netfilter-persistent save` 는 도커 사슬까지
   저장해 쓰지 않는다), 레포 clone(브랜치 인자 — 기본 `claude/build`, main 합친 뒤 `main`), `.env` 템플릿.
3. **`.env` 템플릿**: `.env.example` 이름표를 그대로 복사(권한 600)하고, 기계가 만들 수 있는 값만 무작위로 채운다 —
   `KBJ_POSTGRES_PASSWORD`·`KBJ_REDIS_PASSWORD`·`KBJ_TELEGRAM_WEBHOOK_SECRET`·`KBJ_PUBLIC_EXPORT_DATABASE_URL`
   (`kbj_public_reader` 로그인 역할). `KBJ_LIVE_TRADING=false`·`KBJ_NOTIFY_ENABLED=false`. 키는 사용자가
   `keys.sh`(입력 숨김·글자 수만 표시)나 nano 로, 로그인은 `set-password.sh`(이미지의 `hash-password` 에 stdin 으로 —
   원문은 어디에도 안 남는다). 새 이름 둘: `KBJ_DOMAIN`(사용자 — 선택), `KBJ_SITE_HOST`(start.sh 가 채움).
4. **TLS — Caddy 오버레이**: `docker-compose.vm.yml` 이 `caddy`(공식 `caddy:2.11-alpine`, 다중 아키텍처) 하나를 더한다.
   **호스트 네트워크**(api 루프백에 닿게, uvicorn `forwarded_allow_ips=127.0.0.1` 과 맞게), 비밀 없음, 관리 API 끔,
   HTTP/3 끔(보안 목록이 TCP 만), HSTS·Server 헤더 지우기(나머지 보안 헤더는 앱이 낸다), 접근 로그 없음. 주소는
   `KBJ_DOMAIN` 이 있으면 그 도메인, 없으면 `<공인IP 대시>.sslip.io` — Let's Encrypt 인증서를 Caddy 가 자동 발급·갱신
   (도메인 구매 불필요). `KBJ_PUBLIC_BASE_URL` 을 같은 주소로 맞춘다(텔레그램 웹훅 = 그 주소 + 기존 `WEBHOOK_PATH`).
5. **기동 순서**(start.sh·update.sh 공통 — runbook ③ 그대로): `.env` 필수 이름 검사(이름만 출력) → 주소 → 이미지 빌드(한 번)
   → db·redis(`--wait`) → `migrate` → 공개 내보내기 로그인 역할(ADR 0002 §2.2 — `kbj_public_export` 구성원,
   `default_transaction_read_only=on`, 비밀번호는 stdin·`log_min_error_statement=panic`) → 등록부 `validate` → 전부.
   `gex-poller` 는 등록부 `gex.poller` 가 꺼져 있으면 띄우지 않는다(runbook ④ 전 옛 GX 와 동시 실행 금지 — §9 R2).
6. **업데이트**: `update.sh` = `git pull --ff-only`(고친 파일이 있으면 멈춤) → 받은 새 update.sh 로 이어서 → **업데이트 전
   pg_dump**(볼륨 밖) → 빌드 → migrate → 재기동 → 옛 이미지 정리. 자동 업데이트(cron·워치타워)는 두지 않는다 — 사용자가
   돌린다. 되돌리기는 `git checkout <커밋>` + start.sh, DB 는 백업에서(마이그레이션은 앞으로만).
7. **백업**: 정본은 앱의 `ops.nightly`(03:00 KST, state 볼륨 `/data/backup`, 7일). `backup.sh` 는 그 파일을 볼륨 **밖**
   `~/kbj-backup` 으로 복사해 7일 둔다(cron 04:30 — `--install-cron`). `--now` 만 덤프를 새로 만든다(업데이트 전).
   VM 밖 보관(내 PC·오브젝트 스토리지)은 사용자 결정 [확인 필요 — runbook ⑧].
8. **점검**: `status.sh` = compose 상태·healthcheck·HTTPS 200·인증서 만료·`python -m kbj.services.ops check`(ADR 0037)·
   디스크·메모리·백업 개수. 출력은 이름·상태·개수·시각뿐.
9. **키 안전**: 스크립트는 값을 출력·로그·명령줄 인자에 내지 않는다(`set -x` 없음, `.env` 를 source 하지 않음, 값을
   다루는 줄은 `# 값-내부` 표시 — 시험이 센다). 텔레그램 대화방 번호 찾기(`telegram.sh find-chat`)는 토큰을 curl 설정
   stdin 으로, 대화방은 제목만 보여 주고 번호는 바로 `.env` 에.

## 3. ARM64 확인(2026-10-15, 이 세션)

- 기반 이미지 다중 아키텍처: `python:3.12-slim-trixie`·`node:22-bookworm-slim`·`redis:7-alpine`·`caddy:2.11-alpine`
  (arm64v8), `timescale/timescaledb:latest-pg16`(arm64), `ghcr.io/astral-sh/uv:0.8`(arm64) — 레지스트리 목록으로 확인.
- `uv.lock`: 플랫폼 휠이 있는 패키지 14개(numpy·pandas·scipy·matplotlib·psycopg-binary·pydantic-core·pillow·contourpy·
  kiwisolver·pyyaml·lupa·grimp·hypothesis·ruff) 모두 cp312 `manylinux aarch64` 휠이 잠겨 있다(ruff 는 dev 전용). sdist
  만 있는 패키지 0. `web/package-lock.json` 에 `@esbuild/linux-arm64`·`@rollup/rollup-linux-arm64-gnu` 있음.
- **실제 빌드**: qemu(binfmt) + `docker buildx --platform linux/arm64` 로 레포 `Dockerfile` 을 빌드해 통과(이 세션의
  프록시 때문에 기반 이미지 출처를 ECR 공개 미러로·CA 를 넣은 **임시 사본**으로 — 레포 파일은 그대로). arm64 컨테이너에서
  numpy·pandas·scipy·matplotlib·psycopg(binary)·exchange_calendars·fastapi import, kbj 서비스 모듈 import, 기본 명령
  `scheduler validate` 통과, `hash-password` 동작, pg_dump 17·나눔 글꼴·로그인 SPA 빌드 결과 있음.
- 남은 것 [확인 필요 — VM 첫 기동]: timescale·redis 컨테이너의 실제 arm64 기동, 실제 Ampere 에서 빌드 시간·메모리,
  Let's Encrypt 발급(오라클 보안 목록·iptables 가 열려 있어야). 안내서 ⑥ 의 확인 단계가 이것을 본다.

## 4. 결과

- 사용자가 하는 일: 오라클 가입·VM 만들기·보안 목록 80·443(콘솔 클릭) → 설치 한 줄 → 키 넣기 → start.sh → 텔레그램
  웹훅 → 매일 status.sh. 순서는 `docs/deploy_oracle.md`.
- ADR 0008 의 '레포 밖 TLS' 는 VM 에서 이 오버레이로 바뀐다. 다른 호스팅에서는 기본 compose 만 쓰고 자기 프록시를 둬도
  된다(오버레이는 선택).
- 운영 compose 에는 데모 계정이 없다(데모는 `docker-compose.demo.yml` 따로 — ADR 0034). 시험이 오버레이·Caddyfile·VM
  스크립트·안내서에 데모 값이 없음을 본다.
- 위험: sslip.io(외부 DNS 서비스)가 멈추면 주소가 안 풀린다 — 도메인을 사서 `KBJ_DOMAIN` 으로 바꾸면 된다. 공인 IP 가
  바뀌면(인스턴스 재생성 등) start.sh 가 주소·`KBJ_PUBLIC_BASE_URL` 을 다시 맞추고 웹훅을 다시 걸라고 알린다. 오라클이
  유휴 무료 VM 을 회수할 수 있다(안내서 ⑩).
