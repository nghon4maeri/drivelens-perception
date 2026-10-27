"""`python -m src.rav.cli <lệnh>` — chỉ gọi command, không gọi thẳng pipeline (architecture §2).

- `videos`: command `list_videos`.
- `prepare <video_id>`: command `prepare_video` — chạy P1 cho một video (feature + cache).
- `import-dataset <folder> (--name <name> | --dataset-id <id>) [--annotation-format auto|none|yolo]`: import manifest ảnh/YOLO.
- `benchmark [--recipes …] [--k 200 …] [--seeds 0 1 …] [--videos …] [--metric <type> [key=value …]]…`: command `run_benchmark`,
  mỗi metric một bảng markdown (trung bình ± độ lệch chuẩn theo seed). Không có `--metric` → mặc định `nd_rate_at_k`.
- `spotcheck <video_id> [--distances 0 2 4 …] [--per 4] [--on pool|kept|proposal] [--session <sid>] [--out <file.jpg>]`:
  view `duplicate_pairs` + `get_frame` → lưới ảnh, mỗi hàng một mức khoảng cách pHash, mỗi ô một cặp (a | b); in số cặp theo khoảng cách.
  Không có `--session` → dùng (hoặc tạo) phiên tên `spotcheck:<video_id>` trên pool = video đó (#50).
"""

from __future__ import annotations

import argparse
import io
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from ..commands import COMMANDS, App, ServiceError


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m src.rav.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("videos", help="liệt kê video trong catalog")
    prep = sub.add_parser("prepare", help="chạy P1 cho một video")
    prep.add_argument("video_id")
    dataset = sub.add_parser("import-dataset", help="import dataset ảnh dưới RAV_DATASETS_DIR, không sao chép raw")
    dataset.add_argument("folder")
    identity = dataset.add_mutually_exclusive_group(required=True)
    identity.add_argument("--name")
    identity.add_argument("--dataset-id")
    dataset.add_argument("--annotation-format", choices=["auto", "none", "yolo"], default="auto")
    dataset.add_argument("--description")
    bench = sub.add_parser("benchmark", help="so recipe với baseline bằng ND-Rate@K (state rỗng)")
    bench.add_argument("--recipes", nargs="+")
    bench.add_argument("--k", nargs="+", type=int, default=[200])
    bench.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    bench.add_argument("--videos", nargs="+")
    bench.add_argument("--metric", nargs="+", action="append", metavar="TYPE [KEY=VALUE]",
                       help="lặp được; vd --metric nd_rate_at_k threshold=8")
    spot = sub.add_parser("spotcheck", help="lưới cặp frame theo khoảng cách pHash để chốt ngưỡng ND-Rate")
    spot.add_argument("video_id")
    spot.add_argument("--distances", nargs="+", type=int, default=[0, 2, 4, 6, 8, 10, 12, 14, 16])
    spot.add_argument("--per", type=int, default=4, help="số cặp mỗi mức khoảng cách")
    spot.add_argument("--on", default="pool", choices=["pool", "kept", "proposal"])
    spot.add_argument("--session")
    spot.add_argument("--out")
    args = parser.parse_args()
    app = App.from_env()

    if args.cmd == "videos":
        cmd = COMMANDS["list_videos"]
        for v in cmd.run(cmd.Input(), app).root:
            print(f"{v.id}  {v.duration_s:7.1f}s  {'prepared' if v.prepared else '-':8}  {v.name}")
    elif args.cmd == "import-dataset":
        try:
            out = run(app, "import_dataset", folder=args.folder, name=args.name, dataset_id=args.dataset_id,
                      annotation_format=args.annotation_format, description=args.description)
        except ServiceError as e:
            raise SystemExit(f"{e.code}: {e.message}") from None
        print(json.dumps(out.model_dump(mode="json"), ensure_ascii=False, indent=2))
    elif args.cmd == "prepare":
        cmd = COMMANDS["prepare_video"]
        start = time.perf_counter()
        try:
            out = cmd.run(cmd.Input(video_id=args.video_id), app)
        except ServiceError as e:
            raise SystemExit(f"{e.code}: {e.message}") from None
        print(f"{out.video_id}: {out.n_units} unit, {time.perf_counter() - start:.1f}s")
    elif args.cmd == "benchmark":
        cmd = COMMANDS["run_benchmark"]
        start = time.perf_counter()
        try:
            metrics = {"metrics": [parse_metric(m) for m in args.metric]} if args.metric else {}
            inp = cmd.Input(recipes=args.recipes, K=args.k, seeds=args.seeds, video_ids=args.videos, **metrics)
            out = cmd.run(inp, app)
        except ServiceError as e:
            raise SystemExit(f"{e.code}: {e.message}") from None
        print_benchmark(inp, out)
        print(f"\npool {out.pool_size} unit, {time.perf_counter() - start:.1f}s")

    elif args.cmd == "spotcheck":
        try:
            spotcheck(app, args)
        except ServiceError as e:
            raise SystemExit(f"{e.code}: {e.message}") from None


def run(app, command: str, /, **kw):
    cmd = COMMANDS[command]
    return cmd.run(cmd.Input(**kw), app)


def spotcheck(app, args) -> None:
    sid = args.session
    if sid is None:
        name = f"spotcheck:{args.video_id}"
        found = [s for s in run(app, "list_sessions").root if s.name == name]
        sid = found[0].id if found else run(app, "create_session", pool={"video_ids": [args.video_id]},
                                             goal="spot-check ngưỡng pHash", target_k=1, name=name).id
    view = run(app, "get_view", session_id=sid, name="duplicate_pairs",
               params={"on": args.on, "distances": args.distances, "per_distance": args.per})
    hist, pairs = view.data["hist"], view.data["pairs"]
    total = sum(hist)
    print(f"{total} cặp; khoảng cách d: số cặp đúng d / tích luỹ ≤ d")
    for d in range(max(args.distances) + 1):
        print(f"  d={d:2d}: {hist[d]:7d} / {sum(hist[:d + 1]):7d} ({sum(hist[:d + 1]) / max(total, 1):.2%})")
    out = Path(args.out or f"eval/results/images/spotcheck_{args.video_id}.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    pair_grid(app, args.distances, args.per, pairs).save(out, quality=90)   # PIL mở file bằng Python → đường dẫn có dấu vẫn ghi được
    print(f"lưới: {out}")


def pair_grid(app, distances: list[int], per: int, pairs: list[dict], side: int = 200, label: int = 60) -> Image.Image:
    """Mỗi hàng một mức khoảng cách, mỗi ô một cặp a | b (thumbnail cạnh dài `side`)."""
    cell_w, cell_h, gap = 2 * side + 4, side, 12
    grid = Image.new("RGB", (label + per * (cell_w + gap), len(distances) * (cell_h + gap)), "white")
    draw = ImageDraw.Draw(grid)
    for row, d in enumerate(distances):
        y = row * (cell_h + gap)
        draw.text((6, y + cell_h // 2), f"d={d}", fill="black")
        for col, p in enumerate([p for p in pairs if p["distance"] == d][:per]):
            x = label + col * (cell_w + gap)
            for k, key in enumerate(("a_frame", "b_frame")):
                video_id, idx = p[key]
                img = Image.open(io.BytesIO(run(app, "get_frame", video_id=video_id, idx=idx, max_side=side).data))
                grid.paste(img, (x + k * (side + 4), y))
    return grid


def parse_metric(tokens: list[str]) -> dict:
    """`["nd_rate_at_k", "threshold=8"]` → `{"type": ..., "params": {...}}`; giá trị để chuỗi, Pydantic của metric tự ép kiểu."""
    return {"type": tokens[0], "params": dict(t.split("=", 1) for t in tokens[1:])}


def print_benchmark(inp, out) -> None:
    groups = defaultdict(list)
    for r in out.rows:
        groups[(r.recipe, r.K)].append(r)
    for j, m in enumerate(inp.metrics):
        print(f"\n{m.type} {m.params or ''}".rstrip())
        print("| recipe | K | n | seeds | mean ± std | min–max | độc lập | relaxed |")
        print("|---|---:|---:|---:|---:|---:|---|---|")
        for (recipe, k), rows in groups.items():
            v = np.array([r.metrics[j][m.type] for r in rows])
            indep = "có" if all(r.metrics[j]["independent"] for r in rows) else "**không**"
            print(f"| {recipe} | {k} | {rows[0].n} | {len(rows)} | {v.mean():.2%} ± {v.std():.2%} "
                  f"| {v.min():.2%}–{v.max():.2%} | {indep} | {sum(r.relaxed for r in rows)}/{len(rows)} |")


if __name__ == "__main__":
    main()
