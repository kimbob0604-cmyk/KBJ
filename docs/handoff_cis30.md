# 인계: cis 30분봉 — 분봉 확인·받기·백테스트 (맥 세션용)

맥(로컬 Claude Code 세션)이 이 문서를 위에서부터 따라 한다. 클라우드 세션은 맥 DB·KIS 키에 닿지 않아 코드만
만들어 두었다(커밋 dc6e0e8 이후). 결과는 **요약 숫자만** 사용자에게 보여 주고, 사용자가 클라우드 세션에 붙인다.

## 지킬 것

- 키·토큰·계좌번호를 찍지 않는다. `.env`·토큰 캐시를 열지 않는다(키는 `kbj.sh keys` 로만).
- KIS 원자료(분봉 값·응답 본문)를 저장소에 커밋하지 않는다. probe 출력에는 가격이 없다 — 그대로 붙여도 된다.
- 주문 코드 없음(LIVE_TRADING=false). 백테스트 결과는 화면만.
- 지울 때·덮어쓸 때는 먼저 사용자에게 묻는다.

## 1. 받기·띄우기

```bash
cd ~/kbj && git pull && bash scripts/local/kbj.sh update     # 새 표 prv_market.stock_bar_5m(0029)도 만든다
```

## 2. KIS 분봉 조회 확인 (probe — 1분, 호출 7번)

```bash
bash scripts/local/kbj.sh cis30 probe            # 기본 005930, 다른 종목은 --code 000660
```

확인할 것:

| 줄 | 기대 | 다르면 |
|---|---|---|
| `칸 이름:` | `stck_bsop_date`·`stck_cntg_hour`·`stck_oprc`·`stck_hgpr`·`stck_lwpr`·`stck_prpr`·`cntg_vol` 이 있다 | `kbj/data/private/kis/stock_minute.py` `parse_minutes` 의 칸 이름을 실제 것으로 고치고 `tests/unit/services/cis30_fake.py` 도 같이 |
| 최근 날짜 | `그날 120봉 13:2x:00~15:30:00` 근처(마감 동시호가 15:30 봉 포함) | 15:30 이 없고 15:20 까지면 그대로 둬도 된다(5분 묶기가 받는 대로 묶는다) |
| 날짜가 옛날로 갈수록 | 어느 날짜부터 `그날 0봉` 또는 실패 | 그 앞까지가 보관 기간. 1년보다 짧으면 `fetch --months` 를 그만큼으로 |
| `실패 HTTP … EGW…` | 없어야 한다 | 토큰·키 문제 — `kbj.sh status`, 필요하면 사용자에게 `kbj.sh keys` |

고쳤으면 `.venv` 에서 `pytest tests/unit/kis/test_stock_minute.py tests/unit/services/test_stock_minutes.py tests/unit/services/test_cis30.py`(출력은 파일로) → 커밋·푸시.

## 3. 받을 양 보기 → 받기 (장 마감 뒤·밤에)

```bash
bash scripts/local/kbj.sh cis30 fetch --dry-run                 # 신호 수·(종목, 날) 수·호출 수·예상 시간
bash scripts/local/kbj.sh cis30 fetch --limit 50                # 먼저 50개만 — 오류 없는지
nohup bash scripts/local/kbj.sh cis30 fetch > ~/cis30_fetch.log 2>&1 &   # 나머지 전부(몇 시간)
tail -f ~/cis30_fetch.log
```

- 200개마다 진행 한 줄(받음·빈 날·오류·남은 시간). 끊겨도 다시 돌리면 이미 받은 것은 건너뛴다.
- KIS 앱키 리미터(4/s)를 다른 수집과 나눠 쓰는 P4 — 장중에는 거의 못 받는다. 장 마감 뒤·주말에.
- 오류가 많으면(예: 절반 이상) 멈추고 로그 끝 `오류:` 줄을 사용자에게.
- 기본 대상은 일봉 '통과' 신호(config/screen.yaml `cis30.pool: passed`). 거래량 급증 전부로 넓히려면
  `--pool spikes`(양이 몇 배 — dry-run 으로 먼저 본다).

## 4. 백테스트

```bash
bash scripts/local/kbj.sh cis30 run > ~/cis30_run.txt; cat ~/cis30_run.txt
bash scripts/local/kbj.sh cis30 run --sweep > ~/cis30_sweep.txt; cat ~/cis30_sweep.txt
```

두 파일 내용을 사용자에게 보여 준다(종목 코드·이름·진입/청산 가격이 '최근 거래' 줄에 있다 — 화면에만, 커밋 금지).
첫 줄의 `분봉 있는 (종목, 날) a/b` 에서 a 가 b 보다 많이 작으면 받기가 덜 끝난 것 — 3 을 다시.

## 5. 끝나면 사용자에게

- probe 결과 요약(칸 이름 맞음/고침, 보관 기간)
- fetch 결과(받음·빈 날·오류 수)
- run·sweep 표 그대로
