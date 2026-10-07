-- 004_flags — 기능 플래그를 levels·oi_changes 에도 (docs/phase3_design.md §4, metrics §8)
--
-- metrics 는 003 부터 flag 가 있다. 레벨(Phase 2 핵심 — 기본 visible)과 OI 증감(§6.7 — 새 지표 기본
-- shadow)도 계산 당시 기능 플래그(core/features.py, config/features.yaml)를 남긴다: shadow 는 저장만
-- 하고 발행하지 않는다 — 대시보드(Phase 4)는 visible 만 보이고, 섀도 운영 점검
-- (scripts/shadow_report.py)은 플래그별로 센다.
-- 이미 있는 행의 기본값은 그때 쓰던 값 — 레벨은 늘 발행했으니 visible, oi_changes 는 새 지표 기본
-- shadow. 같은 사이클을 다시 계산하면 DO UPDATE 로 플래그도 최신(data/store.py LEVELS·OI_CHANGES).
--
-- 001~003 은 적용된 뒤라 고치지 않는다(db/migrate.py 체크섬). 두 표 모두 압축하지 않는 hypertable 이라
-- 열 더하기가 청크까지 그대로 간다.

ALTER TABLE levels ADD COLUMN flag text NOT NULL DEFAULT 'visible'
    CHECK (flag IN ('off', 'shadow', 'visible'));

ALTER TABLE oi_changes ADD COLUMN flag text NOT NULL DEFAULT 'shadow'
    CHECK (flag IN ('off', 'shadow', 'visible'));
