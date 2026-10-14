"""README 미디어 — 데모 장면 PNG 들로 GIF 1개 + 정지 화면 PNG + `MANIFEST.txt` 를 만든다.

docs/p9_design.md §4.3. `scripts/demo.sh --gif DIR` 가 부른다(장면은 `demo_shots.mjs --gif`).

    uv run python scripts/make_demo_gif.py <장면 폴더> <출력.gif> [--stills 목록.txt]
        [--now <서버 시계 ISO>] [--command "<만든 명령>"]

- GIF: 장면(`NN_이름.png`, 이름 순)을 폭 960px 로 줄이고(세로는 960×600 캔버스 안에 비율
  그대로), 장마다 256색 적응 팔레트·1.6초·무한 반복. **3MB 를 넘으면 실패**(줄이지 않고 알린다 —
  출력은 지운다).
- 정지 화면: `--stills` 목록의 장면을 `kbj_demo_<이름>.png` 로(원래 크기, 메타데이터 없이).
  1MB 를 넘으면 256색으로 한 번 줄이고, 그래도 넘으면 실패.
- `MANIFEST.txt`: 파일마다 크기·모양, 만든 명령·커밋·합성 세계·시드·서버 시계(검사 ③ —
  `check_public_outputs.py` 가 이 칸들과 파일 목록을 본다). 화면의 종목 이름·숫자는 전부 합성이다.
- 메타데이터: Pillow 로 새로 저장하므로 PNG tEXt/iTXt·GIF 주석 블록이 없다(경로·비밀 형태가
  들어갈 자리 0).

종료 코드: 0 성공 · 1 크기 초과 · 2 사용법(장면 없음 등).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Final

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GIF_WIDTH: Final = 960
GIF_HEIGHT: Final = 600
FRAME_MS: Final = 1600
MAX_GIF: Final = 3 * 1024 * 1024
MAX_PNG: Final = 1024 * 1024
PREFIX: Final = "kbj_demo"
# 합성 세계 — 데모 서버(scripts/demo_server.py)가 싣는 것. 시드는 각 모듈의 SEED(고정)
WORLD: Final = (
    "tests/unit/api/api_world.build_world(scale_market) + p5_world(매크로 — 데모 날짜로 옮김)·"
    "p6_world·p7_world·p8_world (전부 합성 — scripts/demo_server.py)"
)
SEED_MODULES: Final = (
    ("api_world", "tests.unit.api.api_world"),
    ("p5", "tests.fixtures.synthetic.p5_world"),
    ("p6", "tests.fixtures.synthetic.p6_world"),
    ("p7", "tests.fixtures.synthetic.p7_world"),
    ("p8", "tests.fixtures.synthetic.p8_world"),
)
MANIFEST_KEYS: Final = ("command", "commit", "world", "seed", "server_clock")


def _seeds() -> str:
    import importlib

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    parts: list[str] = []
    for name, mod in SEED_MODULES:
        seed = getattr(importlib.import_module(mod), "SEED")  # noqa: B009 — 모듈마다 같은 이름
        parts.append(f"{name}={seed}")
    return " ".join(parts)


def _commit() -> str:
    try:
        out = subprocess.run(  # noqa: S603 — 고정 인자(git rev-parse)
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        return f"(git 없음 — {type(e).__name__})"
    return out.stdout.strip()


def gif_frame(path: Path) -> Image.Image:
    """장면 1장 → 960×600 캔버스(비율 그대로 줄여 가운데) → 256색 적응 팔레트."""
    with Image.open(path) as src:
        im = src.convert("RGB")
    if im.getcolors(maxcolors=8) is not None:  # 색이 8개 이하 — 빈 화면(그리기 전·스크롤 밖)
        raise ValueError(f"빈 장면: {path.name} — 다시 찍는다")
    scale = min(GIF_WIDTH / im.width, GIF_HEIGHT / im.height)
    size = (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
    im = im.resize(size, Image.Resampling.LANCZOS)
    bg = im.getpixel((0, 0))
    canvas = Image.new(
        "RGB", (GIF_WIDTH, GIF_HEIGHT), bg if isinstance(bg, tuple) else (255, 255, 255)
    )
    canvas.paste(im, ((GIF_WIDTH - size[0]) // 2, (GIF_HEIGHT - size[1]) // 2))
    return canvas.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def write_gif(frames: list[Path], out: Path) -> int:
    """GIF 를 쓰고 바이트 수를 낸다. 3MB 초과면 지우고 ValueError."""
    imgs = [gif_frame(p) for p in frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    imgs[0].save(
        out,
        save_all=True,
        append_images=imgs[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
        disposal=1,
    )
    size = out.stat().st_size
    if size > MAX_GIF:
        out.unlink()
        raise ValueError(f"GIF {size:,} B > {MAX_GIF:,} B(3MB) — 장면 수·크기를 줄여 다시 찍는다")
    return size


def write_still(src: Path, out: Path) -> int:
    """정지 화면 PNG(메타데이터 없이 새로 저장).

    1MB 초과면 256색으로 한 번 줄이고, 그래도 넘으면 ValueError."""
    with Image.open(src) as im0:
        im = im0.convert("RGB")
    if im.getcolors(maxcolors=8) is not None:
        raise ValueError(f"빈 장면: {src.name} — 다시 찍는다")
    im.save(out, format="PNG", optimize=True)
    if out.stat().st_size > MAX_PNG:
        im.quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(
            out, format="PNG", optimize=True
        )
    size = out.stat().st_size
    if size > MAX_PNG:
        out.unlink()
        raise ValueError(f"정지 화면 {out.name} {size:,} B > {MAX_PNG:,} B(1MB)")
    return size


def still_name(frame: str) -> str:
    """`03_p3_gex.png` → `kbj_demo_p3_gex.png`."""
    stem = Path(frame).stem
    head, _, rest = stem.partition("_")
    return f"{PREFIX}_{rest if head.isdigit() and rest else stem}.png"


def manifest_text(entries: list[tuple[str, str]], *, command: str, now: str) -> str:
    lines = [
        "# docs/media — README 미디어(합성 데이터 데모 화면 — 종목 이름·숫자는 전부 합성).",
        "# 만든 도구: scripts/make_demo_gif.py (docs/p9_design.md §4.3).",
        "# 검사: scripts/check_public_outputs.py ③(media).",
        f"command: {command}",
        f"commit: {_commit()}",
        f"world: {WORLD}",
        f"seed: {_seeds()}",
        f"server_clock: {now}",
    ]
    lines += [f"file: {name} | {desc}" for name, desc in entries]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="make_demo_gif")
    p.add_argument("frames", type=Path, help="장면 폴더(NN_이름.png)")
    p.add_argument("out", type=Path, help="출력 GIF(같은 폴더에 정지 화면·MANIFEST.txt)")
    p.add_argument(
        "--stills", type=Path, default=None, help="정지 화면 장면 파일 이름 목록(줄마다 하나)"
    )
    p.add_argument("--now", default="", help="데모 서버 시계(ISO) — MANIFEST 에 적는다")
    p.add_argument("--command", default="", help="만든 명령 — MANIFEST 에 적는다")
    p.add_argument("--no-manifest", action="store_true", help="MANIFEST.txt 를 쓰지 않는다(시험용)")
    args = p.parse_args(argv)

    frames = sorted(args.frames.glob("*.png"))
    if not frames:
        print(f"장면이 없다: {args.frames}", file=sys.stderr)
        return 2
    stills: list[str] = []
    if args.stills is not None:
        stills = [
            x.strip() for x in args.stills.read_text(encoding="utf-8").splitlines() if x.strip()
        ]
        missing = [s for s in stills if not (args.frames / s).is_file()]
        if missing:
            print(f"정지 화면 장면이 없다: {', '.join(missing)}", file=sys.stderr)
            return 2
    if not args.no_manifest and (not args.now or not args.command):
        print("MANIFEST 에 적을 --now·--command 가 필요하다", file=sys.stderr)
        return 2
    out_dir = args.out.parent
    entries: list[tuple[str, str]] = []
    try:
        size = write_gif(frames, args.out)
        entries.append(
            (
                args.out.name,
                f"GIF {GIF_WIDTH}×{GIF_HEIGHT} {len(frames)}장 × {FRAME_MS / 1000:.1f}초"
                f" | {size:,} B",
            )
        )
        for s in stills:
            dst = out_dir / still_name(s)
            n = write_still(args.frames / s, dst)
            with Image.open(dst) as im:
                entries.append((dst.name, f"PNG {im.width}×{im.height} (장면 {s}) | {n:,} B"))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if not args.no_manifest:
        (out_dir / "MANIFEST.txt").write_text(
            manifest_text(entries, command=args.command, now=args.now), encoding="utf-8"
        )
    for name, desc in entries:
        print(f"{name}: {desc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
