"""scripts/make_demo_gif.py — GIF 1개(≤3MB)·정지 화면·MANIFEST.txt.

상한 초과·빈 장면은 실패(줄이지 않는다).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import check_public_outputs as cpo
import make_demo_gif as mg


def _scene(path: Path, n: int) -> None:
    im = Image.new("RGB", (1280, 800), (20, 16, 8))
    d = ImageDraw.Draw(im)
    for i in range(12):
        d.rectangle(
            (40 + i * 90, 100 + n * 10, 100 + i * 90, 300 + i * 30), fill=(255, 170 - i * 9, 20 * n)
        )
    d.text((40, 40), f"scene {n}", fill=(255, 200, 0))
    im.save(path, format="PNG")


@pytest.fixture
def frames(tmp_path: Path) -> Path:
    d = tmp_path / "gif"
    d.mkdir()
    for i, name in enumerate(("p1_market", "p3_gex", "p4_flows"), start=1):
        _scene(d / f"{i:02d}_{name}.png", i)
    (d / "stills.txt").write_text("01_p1_market.png\n02_p3_gex.png\n", encoding="utf-8")
    return d


def test_makes_gif_stills_and_manifest_that_pass_media_check(frames: Path, tmp_path: Path) -> None:
    root = tmp_path / "repo"
    media = root / "docs" / "media"
    out = media / "kbj_demo.gif"
    cmd = "bash scripts/demo.sh --gif docs/media"
    rc = mg.main([str(frames), str(out), "--stills", str(frames / "stills.txt"),
                  "--now", "2026-09-15T18:00+09:00", "--command", cmd])  # fmt: skip
    assert rc == 0
    with Image.open(out) as g:
        n_frames = getattr(g, "n_frames", 1)
        assert g.size == (mg.GIF_WIDTH, mg.GIF_HEIGHT) and n_frames == 3
        assert g.info.get("loop") == 0 and g.info.get("duration") == mg.FRAME_MS
    assert out.stat().st_size <= mg.MAX_GIF
    assert sorted(p.name for p in media.iterdir()) == [
        "MANIFEST.txt", "kbj_demo.gif", "kbj_demo_p1_market.png", "kbj_demo_p3_gex.png",
    ]  # fmt: skip
    keys, files = cpo.parse_media_manifest((media / "MANIFEST.txt").read_text(encoding="utf-8"))
    assert all(keys.get(k) for k in cpo.MANIFEST_KEYS)
    assert files == {"kbj_demo.gif", "kbj_demo_p1_market.png", "kbj_demo_p3_gex.png"}
    assert cpo.stage_media(root) == []  # 메타데이터 없음·크기 안·목록 일치


def test_gif_over_cap_fails_and_leaves_nothing(
    frames: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mg, "MAX_GIF", 1000)
    out = tmp_path / "media" / "kbj_demo.gif"
    assert mg.main([str(frames), str(out), "--no-manifest"]) == 1
    assert not out.exists()


def test_blank_scene_is_refused(frames: Path, tmp_path: Path) -> None:
    Image.new("RGB", (1280, 800), (16, 12, 4)).save(frames / "04_blank.png")
    assert mg.main([str(frames), str(tmp_path / "x.gif"), "--no-manifest"]) == 1


def test_usage_errors(tmp_path: Path, frames: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert mg.main([str(tmp_path / "empty"), str(tmp_path / "x.gif"), "--no-manifest"]) == 2
    (frames / "stills.txt").write_text("99_none.png\n", encoding="utf-8")
    assert (
        mg.main(
            [
                str(frames),
                str(tmp_path / "x.gif"),
                "--stills",
                str(frames / "stills.txt"),
                "--no-manifest",
            ]
        )
        == 2
    )
    assert mg.main([str(frames), str(tmp_path / "x.gif")]) == 2  # MANIFEST 칸(--now·--command) 없음


def test_still_name() -> None:
    assert mg.still_name("04_p3_gex.png") == "kbj_demo_p3_gex.png"
    assert mg.still_name("cover.png") == "kbj_demo_cover.png"


def test_committed_media_is_within_limits() -> None:
    """레포의 docs/media — GIF 1개(≤3MB)와 정지 화면 4장(각 ≤1MB, §11 Q4)."""
    media = Path(__file__).resolve().parents[3] / "docs" / "media"
    gifs = sorted(media.glob("*.gif"))
    pngs = sorted(media.glob("*.png"))
    assert len(gifs) == 1 and gifs[0].stat().st_size <= mg.MAX_GIF
    assert len(pngs) == 4 and all(p.stat().st_size <= mg.MAX_PNG for p in pngs)
