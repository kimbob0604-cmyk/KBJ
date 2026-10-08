"""신고가 보드 골든 재캡처 — 같은 합성 입력을 **kbj 엔진(새 정의)** 으로 다시 계산해 기대 산출을
바꾼다.

왜(사용자 요청 2026-10-08, docs/adr/0017-newhigh-three-axes.md): 신고가 정의가 d60(직전
60봉)·w52(직전 252봉)·hist 에서 d120(직전 120 시장 거래일)·w52(달력 364일)·hist 로 바뀌었다. 정의
변경은 사용자 결정이라 '옛 legacy 산출과 같다' 는 골든은 더 이상 맞는 기대값이 아니다. 그래서:

- 입력(합성 일봉·스냅·스칼라·사전 — make_golden.py 가 legacy 로 만든 것)은 **그대로** 둔다. 바꾸는
  것은 둘뿐이다: ① `inputs/common.json.gz` 의 `config.newhigh` 절을 config/board.yaml 의 새 절로,
  ② 날짜별 입력의 `prev_ranks`(전일 기본 기준 라벨 순위)를 **새 엔진의 전일 산출**에서 다시 만든다
  (legacy 도 자기 전일 라벨을 읽었다 — 첫날 day_00 은 골든 창 밖의 전일이라 캡처값 그대로).
- 기대 산출(`expected/day_XX.json.gz`)은 새 엔진 `compute_day` 의 결과다. 전일 newhigh·rankings 는
  `tests/golden/board_golden.iter_days` 와 같이 전날 기대 산출을 읽는다.
- 정의대로 계산하는지는 이 골든이 아니라 독립 오라클(tests/property/test_newhigh_oracle.py ·
  tests/golden/test_board_golden.py 의 오라클 대조)이 지킨다. 골든은 '이 뒤로 산출이 조용히 바뀌지
  않았다' 를 지킨다(META 체크섬).

쓰는 법(레포 루트): ``uv run python tests/golden/board/recapture.py`` — 다시 돌려도 같은
바이트다(gzip mtime=0). 정의를 또 바꾸면 새 ADR 과 함께 다시 돌리고 META 의 `recaptured` 를 고친다.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from kbj.engines.board.build import ENGINE_VERSION, compute_day, prev_ranks_from
from kbj.engines.board.config import BoardConfig

HERE = Path(__file__).resolve().parent
FILES = ("universe", "newhigh", "sectors", "events", "rankings")
RECAPTURED = dict(
    date="2026-10-08",
    reason=(
        "사용자 요청 — 신고가 3축(역사적·52주·120일) 정의 변경(ADR 0017). 입력은 그대로, "
        "config.newhigh 와 전일 순위(prev_ranks)만 새 정의로, 기대 산출은 kbj 엔진으로 다시 계산"
    ),
    engine=f"kbj.engines.board.build.compute_day ({ENGINE_VERSION})",
    adr="docs/adr/0017-newhigh-three-axes.md",
    script="tests/golden/board/recapture.py",
)


def _read(path: Path) -> Any:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _write(path: Path, obj: object) -> None:
    raw = json.dumps(obj, ensure_ascii=False, indent=None).encode()
    with path.open("wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(raw)


def main() -> int:
    # 순환 import 를 피하려고 여기서(board_golden 은 META·입력 파일을 읽는다)
    from tests.golden import board_golden as bg

    common = _read(HERE / "inputs" / "common.json.gz")
    new_nh = BoardConfig.load().to_dict()["newhigh"]
    common["config"]["newhigh"] = new_nh
    BoardConfig.from_mapping(common["config"])  # 새 설정이 검증을 통과하는지
    _write(HERE / "inputs" / "common.json.gz", common)

    meta = json.loads((HERE / "META.json").read_text(encoding="utf-8"))
    basis = new_nh["default_basis"]
    prev_day = None
    for i, _asof in enumerate(meta["days"]):
        raw_path = HERE / "inputs" / f"day_{i:02d}.json.gz"
        raw = _read(raw_path)
        if prev_day is not None:
            raw["prev_ranks"] = prev_ranks_from(
                {x.code: {"rank": x.rank} for x in prev_day.labels if x.basis == basis}
            )
            _write(raw_path, raw)
        # 방금 고친 입력·전날 기대 산출로 그날 BoardInputs 를 만든다(board_golden 과 같은 길)
        day = next(d for d in bg.iter_days(bg.load_common()) if d.index == i)
        got = compute_day(day.inputs, BoardConfig.from_mapping(common["config"]))
        _write(HERE / "expected" / f"day_{i:02d}.json.gz", bg.as_json(got.payloads()))
        prev_day = got
    meta["legacy_engine"] = (
        "입력: legacy/etf_traker/board/engine (shim 전 — D-P3-10 ①) / "
        "기대 산출: kbj 엔진 재캡처(ADR 0017)"
    )
    meta["recaptured"] = RECAPTURED
    meta["checksums"] = {
        str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(HERE.glob("*/*.json.gz"))
    }
    (HERE / "META.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"골든 재캡처 {len(meta['days'])}일 → {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
