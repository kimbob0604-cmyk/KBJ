-- 0022_gex_ingest — GX 수집 경로 KBJ 이식 뒤 쓰기 주인 표기(docs/p9_design.md §7.2 GXI·§9.1, ADR 0036).
--
-- 규칙
-- - 표·칸은 바꾸지 않는다 — 수집 표는 0006·0020 에 이미 있다. 0020 의 COMMENT(쓰기 주인: legacy GX)를
--   KBJ 주인으로 다시 적기만 한다(적용된 마이그레이션 파일은 고치지 않는다 — 체크섬).
-- - 쓰기 주인은 config/jobs.yaml 등록부와 같다: 서비스 gex.poller(kbj.services.gex_poller), 작업
--   gex.day_minutes·gex.night_minutes(kbj.services.collectors.gex_minutes — 분봉·무결측),
--   gex.krx_derivatives(gex_krx), kis.master·kis.master_night(kis_master).
-- - 멱등: COMMENT ON 은 몇 번이고 같은 결과다.

COMMENT ON TABLE prv_gex.chain_snapshots IS '쓰기 주인: gex.poller(KBJ kbj.services.gex_poller — P9).';
COMMENT ON TABLE prv_gex.fut_board IS '쓰기 주인: gex.poller(KBJ kbj.services.gex_poller — P9).';
COMMENT ON TABLE prv_gex.series_expiries IS '쓰기 주인: gex.poller(KBJ kbj.services.gex_poller — P9).';
COMMENT ON TABLE prv_gex.minute_bars IS
    '쓰기 주인: kbj.services.collectors.gex_minutes 처리기 하나(등록부 gex.day_minutes·gex.night_minutes'
    ' — 세션으로 행이 갈림, P9).';
COMMENT ON TABLE prv_gex.krx_fut_daily IS '쓰기 주인: gex.krx_derivatives(KBJ kbj.services.collectors.gex_krx — P9).';
COMMENT ON TABLE prv_gex.krx_opt_daily IS '쓰기 주인: gex.krx_derivatives(KBJ kbj.services.collectors.gex_krx — P9).';
COMMENT ON TABLE prv_gex.master_snapshots IS
    '쓰기 주인: kbj.services.collectors.kis_master 처리기 하나(등록부 kis.master·kis.master_night — P9).';
COMMENT ON TABLE ops.collection_gaps IS
    '쓰기 주인: kbj.services.collectors.gex_minutes 무결측 판정(GapDaily — replace_gap_report, P9).';
COMMENT ON TABLE ops.collection_reports IS
    '쓰기 주인: kbj.services.collectors.gex_minutes 무결측 판정(GapDaily — replace_gap_report, P9).';
COMMENT ON TABLE ops.quarantine IS
    '덧붙이기 전용 사건 기록: gex.poller·gex 분봉 작업(KBJ — P9)이 격리 행을 남긴다(값 원장 아님 — 주인 규칙 밖).';
