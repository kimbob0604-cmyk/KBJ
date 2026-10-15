# KBJ 를 오라클 클라우드 무료 VM 에 띄우기

비개발자용 단계별 안내다. 각 단계는 **무엇을 누르고 무엇을 붙여넣는지**만 적었다. 결정 기록은
[ADR 0040](adr/0040-vm-deploy-oracle-caddy.md), 운영 중 점검·장애 대응은 [runbook](runbook.md).

- 비용: 오라클 **Always Free**(항상 무료) 범위 안에서 0원이다 — Ampere A1(ARM) VM 4 OCPU·메모리 24GB, 블록 저장소 합계
  200GB. 이 범위를 넘는 것을 만들지 않는다.
- 결과: 브라우저 주소 `https://<공인IP 점을 대시로>.sslip.io`(예: IP 가 `1.2.3.4` 면 `https://1-2-3-4.sslip.io`)에서
  로그인 화면이 열리고, 텔레그램으로 아침·마감 브리핑이 온다. 인증서(자물쇠)는 자동이다. 도메인을 살 필요 없다.
- **절대 하지 않는 것**: 키·토큰·비밀번호·계좌번호를 채팅(Claude 포함)·메신저·이슈·스크린샷에 붙이지 않는다. 값은 VM 의
  `.env` 에만 둔다. `KBJ_LIVE_TRADING` 은 `false` 그대로 둔다(실주문 코드는 없다).
- 메뉴 이름은 오라클 콘솔의 영어(한국어) 순서로 적었다. 콘솔 화면은 자주 바뀐다 — 이름이 조금 달라도 같은 자리를 찾으면
  된다. 확실하지 않은 이름은 [확인 필요].

목차: **빠른 길(권장)** · ① 가입 ② VM 만들기(수동으로 할 때) ③ 포트 80·443 열기(수동으로 할 때) ④ 설치 한 줄 ⑤ 키 넣기
⑥ 시작·확인 ⑦ 텔레그램 연결 ⑧ GX → KBJ 전환 ⑨ 매일 점검·업데이트·백업 ⑩ 무료 VM 회수 주의 ⑪ 문제 해결

---

## 빠른 길(권장) — Cloud Shell 에 한 줄

②·③·④ 를 손으로 하지 않고 한 줄로 끝낸다. 오라클 콘솔 안의 검은 창(Cloud Shell)은 이미 내 계정으로 로그인돼 있어
VM·네트워크·방화벽 규칙을 대신 만들 수 있다.

1. ① 대로 가입하고 콘솔(<https://cloud.oracle.com>)에 로그인한다. 오른쪽 위 리전이 **홈 리전**인지 본다.
2. **Cloud Shell 열기**: 콘솔 맨 위 줄 오른쪽의 **`>_` 모양 아이콘(Cloud Shell/Code Editor)** → **Cloud Shell**.
   화면 아래에 검은 창이 열린다(처음은 1분쯤 걸린다). [확인 필요 — 아이콘 모양·메뉴 이름은 콘솔 판에 따라 다르다.
   예전 화면은 **Developer tools → Cloud Shell**]
3. 아래 한 줄을 복사해 검은 창에 붙여넣고 Enter:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/oci_create.sh | bash
   ```

   - 하는 일: 네트워크(`kbj-vcn`·`kbj-subnet`, 들어오는 문은 22·80·443 만) → Ubuntu 24.04 ARM 이미지 → SSH 키
     `~/.ssh/kbj_vm`(Cloud Shell 에만 남는다) → VM `kbj-vm`(무료 A1 4 OCPU·24GB·디스크 100GB·공인 IP). VM 은 켜지면서
     ④ 의 설치를 **스스로** 한다(5~15분).
   - **다시 돌려도 안전하다** — 이미 만든 것은 그대로 쓴다. 중간에 끊겨도 같은 줄을 다시 붙여넣으면 이어서 한다.
   - `[시도 3 · 14:02:11] AD-1 에 kbj-vm … → 자리 없음` 이 반복되면 정상이다. 무료 ARM 자리가 날 때까지 1분마다 최대
     6시간 다시 시도한다. Cloud Shell 은 한동안 입력이 없으면 끊길 수 있어서, 오래 걸리면 **Ctrl+C** 뒤 뒤에서 돌린다:
     `curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/oci_create.sh | bash -s -- --detach`
     (진행 보기 `tail -f ~/.kbj-oci/create.log`. Cloud Shell 이 닫히면 뒤의 작업도 멈출 수 있다 [확인 필요] — 그때는
     같은 줄을 다시)
   - 미리 보기만: 끝에 `-s -- --dry-run`. 만든 것·상태·IP 만 보기: `-s -- --status`.
   - 다른 A1 VM 이 이미 무료 한도를 쓰고 있으면 이유를 말하고 멈춘다(아무것도 만들지 않는다).
4. 끝나면 공인 IP·접속 주소(`https://1-2-3-4.sslip.io` 꼴)와 다음 할 일이 나온다. 그대로 친다:

   ```bash
   ssh -i ~/.ssh/kbj_vm ubuntu@1.2.3.4        # 1.2.3.4 는 화면에 나온 IP. 처음 묻는 yes/no 에는 yes
   tail -f /var/log/kbj-install.log           # 설치 진행 — '== KBJ 설치 끝' 이 보이면 Ctrl+C
   cd ~/kbj && bash scripts/vm/keys.sh && bash scripts/vm/telegram.sh find-chat && bash scripts/vm/set-password.sh
   bash scripts/vm/start.sh
   ```

   키 넣기·텔레그램·비밀번호의 자세한 설명은 ⑤, 시작 확인은 ⑥, 웹훅은 ⑦. 이 길로 왔으면 ②·③·④ 는 건너뛴다(빠른 길의
   SSH 키 이름은 `~/.ssh/kbj_vm` — ② 의 `~/.ssh/kbj` 대신 이것을 쓴다).
5. 모두 지우고 처음부터 하고 싶을 때(VM 의 데이터도 사라진다 — 먼저 ⑨ 대로 백업을 받아 둔다):
   `curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/oci_destroy.sh | bash`
   → `kbj 지우기` 를 그대로 쳐야 지운다.

설치 브랜치를 바꿀 때(작업 브랜치가 main 에 합쳐진 뒤): 주소의 `claude/build` 를 `main` 으로 바꾸고 끝에
`-s -- --branch main`.

## ① 오라클 클라우드 가입

1. <https://www.oracle.com/cloud/free/> 에서 **Start for free**(무료로 시작하기).
2. 이메일·이름·국가(대한민국)를 넣고 메일로 온 링크를 누른다.
3. **Home Region(홈 리전)** 을 고른다: **South Korea Central (Seoul)** 또는 **South Korea North (Chuncheon)**.
   - **한 번 정하면 바꿀 수 없다.** 무료 VM 은 홈 리전에서만 만든다. 둘 중 하나면 된다(KIS·KRX 와 가까움).
   - Ampere A1 무료 자리가 모자라 '용량 없음' 이 자주 나는 리전이 있다 — 어느 쪽이 덜한지는 때마다 다르다 [확인 필요].
4. 카드 등록: **본인 확인용**이다. 몇 달러 승인 후 바로 취소될 수 있다. 무료 계정(Free Tier)은 유료로 바꾸기
   (Upgrade) 전까지 돈이 나가지 않는다.
5. 가입이 끝나면 콘솔(<https://cloud.oracle.com>)에 로그인된다. 오른쪽 위 지역 표시가 홈 리전인지 본다.

## ② VM 만들기 — 수동으로 할 때(Ampere A1 4 OCPU·24GB, Ubuntu 24.04)

빠른 길을 썼으면 건너뛴다.

### ②-1 SSH 키 만들기 — 브라우저 Cloud Shell(프로그램 설치 없음)

1. 콘솔 오른쪽 위 **`>_` 아이콘**(Developer tools → **Cloud Shell**)을 누른다. 화면 아래에 검은 창이 열린다.
2. 아래 두 줄을 복사해 검은 창에 붙여넣고 Enter:

   ```bash
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/kbj
   cat ~/.ssh/kbj.pub
   ```

3. 마지막에 나온 `ssh-ed25519 …` 한 줄(공개 키 — 비밀 아님)을 복사해 둔다. 개인 키(`~/.ssh/kbj`)는 Cloud Shell 에만
   있고 밖으로 보내지 않는다.

### ②-2 인스턴스 만들기

1. 왼쪽 위 **☰ 메뉴 → Compute(컴퓨트) → Instances(인스턴스) → Create instance(인스턴스 생성)**.
2. **Name**: `kbj` 처럼 아무 이름.
3. **Image and shape(이미지와 구성)** → **Edit(편집)**:
   - **Change image(이미지 변경)** → **Ubuntu** → **Canonical Ubuntu 24.04** 를 고르고 **Select image**. (ARM 용
     `aarch64` 판이 셰이프에 맞춰 자동으로 골라진다 [확인 필요 — 목록에 'aarch64' 가 따로 보이면 그것].)
   - **Change shape(구성 변경)** → **Ampere** → **VM.Standard.A1.Flex** 체크 → **Number of OCPUs = 4**,
     **Amount of memory (GB) = 24** → **Select shape**. 'Always Free-eligible(무료 대상)' 표시를 확인한다.
4. **Networking(네트워킹)**: 기본값(새 VCN·공용 서브넷)으로 두고 **Assign a public IPv4 address(공용 IPv4 주소
   지정)** 가 **켜져 있는지** 본다.
5. **Add SSH keys(SSH 키 추가)** → **Paste public keys(공개 키 붙여넣기)** → ②-1 에서 복사한 한 줄을 붙여넣는다.
6. **Boot volume(부트 볼륨)** → **Specify a custom boot volume size(사용자 정의 크기)** 체크 → **100**(GB).
   무료 합계 200GB 안이다(기본 약 47GB 는 DB 가 몇 달 쌓이면 모자랄 수 있다).
7. **Create(생성)**. 1~3분 뒤 상태가 **Running(실행 중)** 이 된다. 화면의 **Public IP address(공용 IP 주소)** 를
   적어 둔다(예 `1.2.3.4`).
   - **'Out of capacity(용량 부족)'** 이 나오면: 몇 시간 뒤나 새벽에 다시 **Create**. 서울은 가용성 도메인(AD)이
     하나라 바꿀 수 없다. 계속 안 되면 ⑩ 의 '유료 계정 전환' 이 자리를 얻는 데 도움이 된다고 알려져 있다 [확인 필요].

## ③ 포트 80·443 열기(보안 목록) — 수동으로 할 때

빠른 길을 썼으면 건너뛴다(`kbj-sl` 이 이미 22·80·443 을 연다).

인증서 발급과 접속에 80(HTTP)·443(HTTPS)이 바깥에서 열려 있어야 한다. VM 안 방화벽은 ④ 의 설치가 연다 — 여기서는
오라클 쪽 문을 연다.

1. 인스턴스 화면의 **Primary VNIC(기본 VNIC)** 칸에서 **Subnet(서브넷)** 이름을 누른다.
   (또는 **☰ → Networking(네트워킹) → Virtual cloud networks(가상 클라우드 네트워크) → 만든 VCN → Subnets**.)
2. **Security Lists(보안 목록)** → **Default Security List for …(기본 보안 목록)** 을 누른다.
   (새 콘솔에서는 VCN 화면의 **Security(보안)** 탭 안에 있다 [확인 필요].)
3. **Add Ingress Rules(수신 규칙 추가)** 를 누르고 아래처럼 넣는다:
   - **Source CIDR(소스 CIDR)**: `0.0.0.0/0`
   - **IP Protocol(IP 프로토콜)**: TCP
   - **Destination Port Range(대상 포트 범위)**: `80,443`
   - **Add Ingress Rules** 누르기.
4. 목록에 22(SSH)·80·443 이 있으면 끝이다. 다른 포트(5432·6379·8000)는 **열지 않는다** — DB·Redis·API 는 VM 안에서만
   쓴다.

## ④ 접속해서 설치 한 줄

빠른 길을 썼으면 VM 이 이 설치를 스스로 했다 — 위 빠른 길 4 의 `tail -f /var/log/kbj-install.log` 로 끝났는지만 본다.

1. Cloud Shell(②-1 의 검은 창)에서 — `1.2.3.4` 를 내 공인 IP 로 바꿔서:

   ```bash
   ssh -i ~/.ssh/kbj ubuntu@1.2.3.4
   ```

   처음 묻는 `Are you sure you want to continue connecting (yes/no)?` 에는 `yes`. 프롬프트가 `ubuntu@kbj:~$` 처럼
   바뀌면 VM 안이다.
2. 아래 한 줄을 붙여넣고 Enter(5~10분):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/install.sh | bash
   ```

   - 작업 브랜치가 `main` 에 합쳐진 뒤에는 대신:
     `curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/main/scripts/vm/install.sh | bash -s -- --branch main`
   - 같은 것을 레포를 받아서 할 수도 있다:
     `git clone -b claude/build https://github.com/kimbob0604-cmyk/KBJ.git ~/kbj && bash ~/kbj/scripts/vm/install.sh`
3. 하는 일: Docker 설치, 시간대 Asia/Seoul·시각 동기화, VM 안 방화벽 80·443, 레포를 `~/kbj` 에 받기, `.env` 만들기
   (DB·Redis 비밀번호·웹훅 비밀은 자동 생성 — 화면에 안 나온다). **다시 돌려도 안전하다.**
4. 끝에 `✖ 비어 있는 필수 이름: …` 이 보이는 것이 정상이다 — ⑤ 에서 채운다.
5. 이후 모든 명령은 `cd ~/kbj` 한 뒤에 친다.

## ⑤ 키 넣기(`.env`)

이름표 정본은 [docs/secrets.md](secrets.md). 지금 꼭 필요한 것과 받는 곳:

| 이름 | 무엇 | 어디서 받나 | 필수 |
|---|---|---|---|
| `KBJ_KIS_APP_KEY`·`KBJ_KIS_APP_SECRET` | 한국투자증권 앱키·앱시크릿 | KIS Developers(<https://apiportal.koreainvestment.com>) → API 신청(실전투자) | 예 |
| `KBJ_TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | 텔레그램에서 `@BotFather` → `/newbot` → 이름 두 번 → 토큰 | 예 |
| `KBJ_TELEGRAM_CHAT_ID` | 알림 받을 대화방 번호 | 아래 3 의 `telegram.sh find-chat` 이 찾아 넣는다 | 예 |
| `KBJ_WEB_USER`·`KBJ_WEB_PASSWORD_HASH` | 로그인 아이디·비밀번호 | 아래 4 의 `set-password.sh` | 예 |
| `KBJ_KRX_API_KEY` | KRX OpenAPI 인증키 | KRX 정보데이터시스템 OpenAPI(<https://openapi.krx.co.kr>) → 인증키 신청 + 쓰는 서비스 이용 신청 | 권장 |
| `KBJ_DART_API_KEY` | OpenDART 인증키 | <https://opendart.fss.or.kr> → 인증키 신청 | 권장 |
| `KBJ_POSTGRES_PASSWORD`·`KBJ_REDIS_PASSWORD`·`KBJ_TELEGRAM_WEBHOOK_SECRET`·`KBJ_PUBLIC_EXPORT_DATABASE_URL` | 내부 비밀번호 | **자동 생성됨 — 손대지 않는다** | — |

ECOS·KOSIS·FRED·공공데이터포털 키는 지금 켜진 작업이 쓰지 않는다(그 작업들은 등록부에서 꺼져 있다) — 나중에.

1. 키 넣기(입력은 화면에 **안 보인다** — 붙여넣고 Enter. 이미 있는 것은 Enter 만 치면 그대로):

   ```bash
   bash scripts/vm/keys.sh
   ```

   하나만 바꿀 때: `bash scripts/vm/keys.sh KBJ_DART_API_KEY`. 채워졌는지만 볼 때: `bash scripts/vm/keys.sh --list`.
   (익숙하면 `nano .env` 로 직접 고쳐도 된다 — 저장 Ctrl+O, Enter, 나가기 Ctrl+X.)
2. 텔레그램 그룹 준비: 텔레그램에서 새 그룹을 만들고 ①에서 만든 봇을 **멤버로 추가**(관리자로 올리면 토픽에도 쓸 수 있다).
   그룹에 `/start` 를 한 번 보낸다.
3. 대화방 번호 찾기(그룹 **제목만** 보여 주고 번호는 바로 `.env` 에 들어간다):

   ```bash
   bash scripts/vm/telegram.sh find-chat
   ```

4. 로그인 아이디·비밀번호(비밀번호 12자 이상, 처음 한 번은 이미지 빌드로 10~20분):

   ```bash
   bash scripts/vm/set-password.sh
   ```

## ⑥ 시작·확인

1. 시작(처음은 빌드 때문에 10~20분, 다음부터 1~2분):

   ```bash
   bash scripts/vm/start.sh
   ```

   - 필수 이름이 비었으면 이름만 보여 주고 멈춘다(값은 보여 주지 않는다).
   - 순서: 빌드 → DB·Redis → 마이그레이션 → 공개 내보내기 역할 → 작업 등록부 검증 → 모든 서비스 → HTTPS 확인.
   - 끝에 `브라우저 주소: https://1-2-3-4.sslip.io` 가 나온다.
2. PC·폰 브라우저에서 그 주소를 연다 → 자물쇠 표시 → ⑤-4 의 아이디·비밀번호로 로그인 → 페이지 13 '운영' 에서 서비스가
   초록인지 본다.
3. VM 에서 한 번 점검:

   ```bash
   bash scripts/vm/status.sh
   ```

   컨테이너 표가 모두 `running`(healthcheck 있는 것은 `healthy`), `https://… → 200`, 운영 점검(`ops check`) 종료 코드 0
   이면 정상이다. 막 띄운 직후에는 작업 기록이 비어 WARN 이 있을 수 있다.
4. **ARM 첫 기동 확인**(이 레포는 ARM 빌드를 시험 환경에서 확인했지만 실제 Ampere 에서는 처음이다 — ADR 0040 §3):
   위 3 이 통과하면 확인된 것이다. `db`·`redis` 가 `restarting` 이거나 빌드가 `exec format error` 로 멈추면 ⑪ 의
   'ARM 관련' 을 본다. 결과(통과/실패·빌드 시간)만 runbook 이나 메모에 적는다.

## ⑦ 텔레그램 웹훅 연결

텔레그램이 봇에게 온 메시지를 `https://<주소>/telegram/webhook` 으로 보내게 건다(경로는 KBJ 코드의 고정값).

```bash
bash scripts/vm/telegram.sh check          # 봇·대화방 확인(메시지는 안 보낸다)
bash scripts/vm/telegram.sh webhook        # 웹훅 걸기 → 이어서 상태 표시(url_set: true, last_error 없음)
bash scripts/vm/telegram.sh enable-send    # 발송 켜기(기본은 꺼짐 — 기록만 한다)
```

- 웹훅을 건 뒤에는 `find-chat` 이 목록을 못 받는다(텔레그램 규칙). 대화방을 바꾸려면 `.env` 의
  `KBJ_TELEGRAM_CHAT_ID` 를 nano 로.
- 발송을 끄려면 `bash scripts/vm/telegram.sh disable-send`.
- 공인 IP 나 도메인이 바뀌면 start.sh 가 알려 준다 — 그때 `telegram.sh webhook` 을 다시.

## ⑧ GX → KBJ 전환

옛 GX(GEX 수집)가 아직 다른 곳에서 돌고 있으면 KBJ 의 `gex-poller` 와 GX 수집 작업은 **꺼진 채**로 둔다(같은 KIS 앱키로
둘이 동시에 돌면 한도 초과·중복). start.sh 도 등록부에서 `gex.poller` 가 꺼져 있으면 `gex-poller` 를 띄우지 않는다.

전환은 **주말·장이 닫힌 시간**에 한 번 — 순서는 [runbook ④](runbook.md).
VM 에서는 runbook 의 `docker compose --profile app …` 대신 `bash scripts/vm/update.sh`(등록부 커밋을 받은 뒤)로 다시
띄우면 `gex-poller` 가 같이 뜬다. `run-once kis.master` 같은 한 번짜리 명령은 runbook 그대로
`docker compose -f docker-compose.yml -f docker-compose.vm.yml --profile app exec scheduler …` 로.

## ⑨ 매일 점검·업데이트·백업

| 언제 | 명령 | 볼 것 |
|---|---|---|
| 장 시작 전(08:30)·마감 브리핑 뒤(16:30) | `bash scripts/vm/status.sh` | 모두 healthy, HTTPS 200, ops check 종료 코드 0 |
| 주말 뒤 월요일 | `bash scripts/vm/status.sh --since 3d` | 같은 것, 3일치 |
| 서비스 하나가 이상할 때 | `bash scripts/vm/status.sh --logs <서비스>` | 최근 2시간 로그(키는 가려져 있다). 이어서 runbook ⑥ |
| 새 버전이 올라왔을 때 | `bash scripts/vm/update.sh` | 백업 → 받기 → 빌드 → 마이그레이션 → 재기동 |
| main 에 합친 뒤 한 번 | `bash scripts/vm/update.sh --branch main` | 이후 `update.sh` 는 main 을 따른다 |
| 처음 한 번 | `bash scripts/vm/backup.sh --install-cron` | 매일 04:30 백업 복사 등록 |
| 아무 때나 | `bash scripts/vm/backup.sh --now` | 지금 바로 DB 덤프 |
| 잠깐 멈출 때 | `bash scripts/vm/stop.sh` → 다시 `bash scripts/vm/start.sh` | 데이터는 그대로 |

- 백업: 앱이 매일 03:00 에 DB 덤프를 만든다(7일). `backup.sh` 는 그것을 도커 밖 `~/kbj-backup` 에 복사해 7일 둔다.
  VM 이 통째로 사라지는 경우까지 대비하려면 가끔 내 PC 로 내려받는다 — Cloud Shell 에서
  `scp -i ~/.ssh/kbj ubuntu@1.2.3.4:kbj-backup/kbj-*.dump .` 뒤 Cloud Shell 메뉴의 **Download(다운로드)** [확인 필요].
  복구 연습은 runbook ⑧.
- 1주 무중단 판정·공개 사이트 켜기는 runbook ⑩·⑦.

## ⑩ 무료 VM 회수 주의

- 오라클은 **오래 거의 놀고 있는 Always Free 인스턴스를 회수(정지)** 할 수 있다고 공지한다 — 7일 동안 CPU·네트워크·
  (A1 은) 메모리 사용률이 아주 낮으면 대상이다 [확인 필요 — 정확한 기준은 오라클 Always Free 문서의 'Idle Always Free
  compute instances'].
- KBJ 는 장중 웹소켓 녹화·수집·스케줄러가 늘 돌아 완전히 놀지는 않지만, 주말·연휴가 길면 낮아질 수 있다.
- 권하는 방법: 계정을 **Pay As You Go(종량제)로 업그레이드**(☰ → Billing & Cost Management(청구 및 비용 관리) →
  Upgrade and Manage Payment(업그레이드) [확인 필요]). Always Free 자원은 계속 무료이고 회수 대상에서 빠진다고 알려져
  있다. 대신 무료 범위를 넘는 것을 만들면 돈이 나가므로 **Budgets(예산)** 에서 1달러 알림을 걸어 둔다.
- 사용률을 억지로 올리는 프로그램은 돌리지 않는다(KIS·VM 자원을 낭비하고 약관 문제가 될 수 있다).
- 회수되면 콘솔에서 인스턴스를 **Start(시작)** — 부트 볼륨(데이터)은 남는다. 그 뒤 `bash scripts/vm/start.sh`.

## ⑪ 문제 해결

| 증상 | 할 일 |
|---|---|
| 브라우저가 안 열린다 / 인증서 오류 | ③ 의 80·443 수신 규칙 확인 → VM 에서 `bash scripts/vm/install.sh`(방화벽 다시) → `bash scripts/vm/status.sh --logs caddy`. 첫 발급은 1~2분 걸린다 |
| `Out of capacity` 로 VM 이 안 만들어진다 | 빠른 길의 한 줄이 자리가 날 때까지 다시 시도한다(`--detach` 로 뒤에서). 6시간 넘게 안 되면 새벽에 다시. ⑩ 의 종량제 전환 |
| 빠른 길이 `✖ … 실패:` 로 멈췄다 | 그 줄의 쉬운 설명대로. '원문 요약' 줄(키·비밀번호 없음)은 개발 쪽에 그대로 전달해도 된다 |
| 빠른 길의 VM 에서 설치가 실패했다(`== KBJ 설치 실패`) | VM 에서 `curl -fsSL https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm/install.sh \| bash` 로 다시 |
| `docker: permission denied` | 로그아웃(`exit`) 뒤 다시 접속(설치가 docker 그룹에 넣었다). 스크립트는 그 전에도 sudo 로 대신한다 |
| `✖ 비어 있는 필수 이름: …` | 그 이름을 ⑤ 대로 채운다(`keys.sh`·`set-password.sh`·`telegram.sh find-chat`) |
| 로그인이 503 | `KBJ_WEB_USER`·`KBJ_WEB_PASSWORD_HASH` 가 비었다 — `bash scripts/vm/set-password.sh` |
| 비밀번호를 잊었다 | `bash scripts/vm/set-password.sh` 로 새로 정한다(옛 것은 복구할 수 없다) |
| 로그인이 잠겼다 | 몇 번 틀리면 그 IP 가 잠시 잠긴다 — 기다렸다가 다시 |
| `.env` 를 지웠다 / 새로 만들었다 | DB·Redis 비밀번호가 새로 만들어져 이미 있는 DB 와 맞지 않는다. 옛 `.env` 를 되살리는 것이 가장 쉽다(그래서 `.env` 를 비밀번호 관리자 같은 안전한 곳에 따로 보관해 둔다). 옛 것이 없으면 데이터를 지우지 말고 개발 쪽에 'DB 비밀번호 다시 맞추기' 를 요청한다(값은 보내지 않는다) |
| `public.export` 작업이 실패로 남는다 | `KBJ_PUBLIC_EXPORT_DATABASE_URL` 을 손으로 바꿨으면 원래 형식(`postgresql://kbj_public_reader:…@db:5432/kbj`)으로 되돌리고 start.sh(역할을 다시 맞춘다) |
| 텔레그램이 안 온다 | `telegram.sh check` → `telegram.sh info`(last_error) → `enable-send` 했는지. 봇이 그룹에 있는지 |
| `update.sh` 가 '고친 파일이 있다' 로 멈춘다 | VM 에서 레포 파일을 고쳤다. `cd ~/kbj && git status` 로 보고 `git checkout -- <파일>` 로 되돌린 뒤 다시 |
| 디스크가 찬다 | `status.sh` 의 디스크 칸·runbook ⑥ 'DB 디스크 부족'. 부트 볼륨은 콘솔에서 늘릴 수 있다(무료 합계 200GB) [확인 필요 — 늘린 뒤 VM 안에서 파티션 확장 필요] |
| ARM 관련: 빌드가 `exec format error`·휠 없음으로 멈춘다 | `uname -m` 이 `aarch64` 인지 확인. 그대로 오류 마지막 20줄(키 없음)을 개발 쪽에 전달 — ADR 0040 §3 은 시험 환경에서 통과를 확인했다 |
| 공인 IP 가 바뀌었다 | `bash scripts/vm/start.sh` 가 새 주소로 맞추고 알려 준다 → `telegram.sh webhook`. IP 가 안 바뀌게 하려면 콘솔에서 Reserved public IP(예약된 공용 IP)로 바꾼다 [확인 필요] |
| 내 도메인을 쓰고 싶다 | 도메인 DNS 에 A 레코드(→ VM 공인 IP)를 만든 뒤 `bash scripts/vm/keys.sh KBJ_DOMAIN` → `start.sh` → `telegram.sh webhook` |
| 다 멈췄다 | `bash scripts/vm/stop.sh` → `bash scripts/vm/start.sh`. 그래도 안 되면 콘솔에서 인스턴스 **Reboot(재부팅)** — 컨테이너는 자동으로 다시 뜬다 |
