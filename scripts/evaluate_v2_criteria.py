"""Evaluate real cached V2 retrieval without modifying the user's store.

Input: existing ui-smoke-v2 reference fixture and main cached videos. Output: isolated
store, JSON checks and contact sheets under output/v2-criteria-eval (all paths relative).
Run: python -m scripts.evaluate_v2_criteria audit|run|report. No downloads or Kaggle.
Manual labels.json is independent visual judgment: {case: {recipe: {ids, labels}}}.
Protocol: docs/evaluation/V2_CRITERIA.md; query/thresholds must not be tuned to results.
"""
import argparse
import json
import os
import queue
import shutil
import sqlite3
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from src.rav.core.registry import REGISTRY, discover
from src.rav.core.types import DatasetReference, PoolFilter
from src.rav.pipeline import batch_yolo
from src.rav.pipeline import prepare as p1
from src.rav.pipeline.propose import get_score_candidates, propose
from src.rav.service import App, proposal, proposal_jobs, review_state, session

ROOT = Path("output/v2-criteria-eval")
FIXTURE = Path("output/ui-smoke-v2")
VIDEO_IDS = ["c39d526f34dfba46", "54b2100189d019df"]
SEED = 20261007
QUERIES = {
    "cars": "a dashcam photo of cars on a road",
    "highway": "a dashcam photo of a highway",
    "traffic_light": "a dashcam photo of a road with a visible traffic light",
    "night": "a dashcam photo of a road at night",
    "snow": "a dashcam photo of a snow covered road",
    "dog": "a dashcam photo of a dog on the road",
}


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def setup():
    ROOT.mkdir(parents=True, exist_ok=True)
    app = App(ROOT / "videos", ROOT / "store", 500 << 20,
              datasets_dir=ROOT / "datasets", models_dir=ROOT / "models")
    if not (app.output_dir / "rav.sqlite").exists():
        app.output_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(FIXTURE / "store/rav.sqlite") as source, sqlite3.connect(app.output_dir / "rav.sqlite") as dest:
            source.backup(dest)
        for src, dst in ((FIXTURE / "store/fields", app.fields.root),
                         (FIXTURE / "datasets", app.datasets_dir), (FIXTURE / "models", app.models_dir)):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        main = App.from_env()
        catalog = p1.catalog(main.spec)
        app.videos_dir.mkdir(parents=True, exist_ok=True)
        for vid in VIDEO_IDS:
            shutil.copy2(catalog[vid].uri, app.videos_dir / Path(catalog[vid].uri).name)
            shutil.copytree(main.fields.root / vid, app.fields.root / vid, dirs_exist_ok=True)
    fixture = json.loads((FIXTURE / "fixture.json").read_text())
    app.prepare_queue = queue.Queue()  # Execute the very same queued worker sequentially in this CLI.
    return app, fixture


def new_session(app, fixture, goal):
    return session.create_session(app, PoolFilter(video_ids=VIDEO_IDS), goal, 100,
        "V2 criteria evaluation (simulated training relationship)",
        DatasetReference(dataset_version_ids=[fixture["version"]], model_id=fixture["model_id"]))["id"]


def sheet(ctx, units, path, title, labels=None):
    width, height, cols = 320, 208, 4
    rows = (len(units) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * width, max(1, rows) * height + 36), "#f1f5f9")
    draw = ImageDraw.Draw(canvas)
    draw.text((8, 10), title, fill="black")
    for i, unit in enumerate(units):
        im = Image.fromarray(ctx.pixels_exact(unit.anchor))
        im.thumbnail((width, height - 36))
        x, y = (i % cols) * width, (i // cols) * height + 36
        canvas.paste(im, (x + (width - im.width) // 2, y))
        label = labels[i] if labels else str(i)
        draw.text((x + 5, y + height - 31), f"{label} | {unit.video_id[:5]} t={unit.t0:.2f}s", fill="black")
    canvas.save(path)


def audit(app, fixture):
    sid = new_session(app, fixture, "Pool inspection before retrieval")
    ctx = proposal.session_context(app, sid)
    write(ROOT / "pool.json", [{"index": i, "id": u.id, "t": u.t0, "video": u.video_id} for i, u in enumerate(ctx.units)])
    # Review the entire pool in chronological pages, not just a favorable subset.
    for start in range(0, len(ctx.units), 40):
        units = ctx.units[start:start + 40]
        sheet(ctx, units, ROOT / f"pool-{start:03}.jpg", f"Pool audit {start}..{start + len(units) - 1}",
              [str(i) for i in range(start, start + len(units))])
    print(json.dumps({"pool": len(ctx.units), "audit_pages": len(list(ROOT.glob('pool-*.jpg')))}))


def recipe(name, text, negative=None, threshold=.2):
    cfg = REGISTRY.recipe(name).model_copy(deep=True)
    if cfg.candidate_stage:
        cfg.candidate_stage.limit = 16
        objectives = cfg.candidate_stage.objectives
    else:
        objectives = cfg.objectives
    for objective in objectives:
        if objective.type == "text_match":
            objective.params = {"texts": [text], "negative": negative or [], "min_similarity": threshold}
    return cfg


def gates(ctx, units, sel):
    params = REGISTRY.get("selector", sel.type).Params.model_validate(sel.params)
    vectors = np.asarray(ctx.field(params.field, units).values, dtype=np.float64) if units else np.empty((0, 1))
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    for i, unit in enumerate(units):
        for j in range(i):
            other = units[j]
            if unit.video_id == other.video_id:
                assert abs(unit.t0 - other.t0) >= params.nms_s - 1e-6
            assert 1 - vectors[i] @ vectors[j] >= params.min_dist - 1e-6


def evaluate(app, fixture):
    calls = []
    detect = batch_yolo.detect

    def counted(*args, **kwargs):
        calls.append(1)
        return detect(*args, **kwargs)

    batch_yolo.detect = counted  # Count real engine calls; no mock predictions.
    cases = [(key, text, [], .2) for key, text in QUERIES.items()] + [
        ("exclude_highway", QUERIES["cars"], [QUERIES["highway"]], .2),
        ("equal_negative", QUERIES["cars"], [QUERIES["cars"]], .2),
        ("threshold_one", QUERIES["cars"], [], 1.),
    ]
    results = []
    for key, text, negative, threshold in cases:
        for name in ("text_diversity", "text_model_guided"):
            sid = new_session(app, fixture, text)
            cfg = recipe(name, text, negative, threshold)
            bound = proposal.bind_recipe(app, sid, cfg)
            ctx = proposal.session_context(app, sid, bound)
            by_id = {u.id: u for u in ctx.units}
            before_calls = len(calls)
            started = time.monotonic()
            if cfg.candidate_stage:
                proposal_jobs.queue(app, sid, name, cfg, 8, SEED)
                app.prepare_queue.get()()
                job = proposal_jobs.list_jobs(app, sid)[0]
                assert job["state"] == "done", job
                rec = app.db.proposals(sid)[-1]
            else:
                rec = proposal.create_proposal(app, sid, name, cfg, 8, SEED)
            engine_calls = len(calls) - before_calls
            ids = [p["unit_id"] for p in rec["picks"]]
            assert len(ids) == len(set(ids)) and set(ids) <= set(by_id)
            assert not set(ids) & {u.id for units in ctx.reference_sets.values() for u in units}
            gates(ctx, [by_id[i] for i in ids], cfg.selector)
            cheap = bound.model_copy(update={"objectives": bound.candidate_stage.objectives,
                "combiner": bound.candidate_stage.combiner, "selector": bound.candidate_stage.selector,
                "candidate_stage": None}) if bound.candidate_stage else bound
            candidates, objective_scores, _, mask = get_score_candidates(cheap, ctx)
            valid = {u.id for u, m in zip(candidates, mask) if m}
            stage_ids = rec["input"].get("execution", {}).get("candidate_ids", ids)
            assert set(ids) <= valid
            if cfg.candidate_stage:
                assert set(stage_ids) <= valid and len(stage_ids) <= 16
                gates(ctx, [by_id[i] for i in stage_ids], cfg.candidate_stage.selector)
                assert engine_calls <= 2 * len(stage_ids)
            else:
                assert engine_calls == 0
            replay_before = len(calls)
            scores = proposal.scores(app, sid, rec)
            assert len(calls) == replay_before
            for values in (v for k, v in scores.items() if k != "unit_id"):
                assert np.isfinite(values).all() and all(0 <= v <= 1 for v in values)
            for pick in rec["picks"]:
                assert np.isclose(pick["combined"], scores["combined"][scores["unit_id"].index(pick["unit_id"])])
            assert app.db.decisions(sid) == []
            repeated_ctx = proposal.session_context(app, sid, bound)
            repeat = propose(bound, 8, SEED, repeated_ctx, stage_ids if cfg.candidate_stage else None)
            assert [p.unit.id for p in repeat.picks] == ids
            assert np.allclose([p.combined for p in repeat.picks], [p["combined"] for p in rec["picks"]])
            assert len(calls) == replay_before
            if key in ("equal_negative", "threshold_one"):
                assert ids == [] and engine_calls == 0
            uniform = propose(REGISTRY.recipe("uniform"), len(ids), SEED, proposal.session_context(app, sid)) if ids else None
            baseline_ids = [p.unit.id for p in uniform.picks] if uniform else []
            sheet(ctx, [by_id[i] for i in ids], ROOT / f"{key}-{name}.jpg", f"{key} / {name}: {len(ids)} picks")
            sheet(ctx, [by_id[i] for i in baseline_ids], ROOT / f"{key}-{name}-uniform.jpg", f"{key} / uniform: {len(baseline_ids)} picks")
            match = next(s for s in objective_scores if s.name == "text_match")
            result = {"case": key, "recipe": name, "sid": sid, "pool": len(ctx.units), "eligible": len(valid),
                "candidate_count": len(stage_ids) if cfg.candidate_stage else len(candidates), "ids": ids,
                "uniform_ids": baseline_ids, "detector_calls": engine_calls, "seconds": round(time.monotonic() - started, 2),
                "max_cosine": float(np.max(2 * match.values - 1)), "config": rec["input"]["config"],
                "config_hash": rec["input"]["config_hash"], "scores": scores, "checks": "passed"}
            results.append(result)
            write(ROOT / "results.json", results)
            print(json.dumps({k: result[k] for k in ("case", "recipe", "eligible", "candidate_count", "detector_calls", "seconds")}), flush=True)
    # Review exclusion on a dedicated test session, preserving saved replay.
    sid = new_session(app, fixture, "review test")
    first = proposal.create_proposal(app, sid, "reference_diversity", None, 8, SEED)
    assert len(first["picks"]) >= 2
    removed = [p["unit_id"] for p in first["picks"][:2]]
    review_state.update_state(app, sid, [{"unit_id": removed[0], "action": "keep"}, {"unit_id": removed[1], "action": "drop"}])
    second = proposal.create_proposal(app, sid, "reference_diversity", None, 8, SEED)
    assert not set(removed) & {p["unit_id"] for p in second["picks"]}
    # A deliberately restrictive time gate must return fewer than requested, never relax.
    sid = new_session(app, fixture, "strict NMS boundary")
    cfg = recipe("text_model_guided", QUERIES["cars"])
    cfg.selector.params["nms_s"] = cfg.candidate_stage.selector.params["nms_s"] = 100.
    before_calls = len(calls)
    proposal_jobs.queue(app, sid, cfg.name, cfg, 8, SEED)
    app.prepare_queue.get()()
    assert proposal_jobs.list_jobs(app, sid)[0]["state"] == "done"
    strict = app.db.proposals(sid)[-1]
    assert len(strict["picks"]) == 2 and not strict["relaxed"]
    assert len(strict["input"]["execution"]["candidate_ids"]) == 2
    assert len(calls) - before_calls <= 4
    strict_calls = len(calls) - before_calls
    # Goal is metadata for model_guided, not an implicit CLIP query.
    goal_results = []
    for goal in (QUERIES["cars"], QUERIES["snow"]):
        sid = new_session(app, fixture, goal)
        cfg = REGISTRY.recipe("model_guided").model_copy(deep=True)
        cfg.candidate_stage.limit = 16
        proposal_jobs.queue(app, sid, "model_guided", cfg, 8, SEED)
        app.prepare_queue.get()()
        assert proposal_jobs.list_jobs(app, sid)[0]["state"] == "done"
        goal_results.append([p["unit_id"] for p in app.db.proposals(sid)[-1]["picks"]])
    assert goal_results[0] == goal_results[1]
    write(ROOT / "checks.json", {"seed": SEED, "review_exclusion": True, "goal_not_used_by_model_guided": True,
         "strict_nms_requested": 8, "strict_nms_returned": len(strict["picks"]),
         "strict_nms_detector_calls": strict_calls,
         "goals_same_ids": goal_results, "all_cases_pass_contract": True, "training_reference": "simulated, not nano training data"})


def report():
    results = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
    label_path = ROOT / "labels.json"
    if not label_path.exists():
        label_path = Path("docs/evaluation/V2_VISUAL_LABELS.json")
    labels = json.loads(label_path.read_text(encoding="utf-8"))
    summary = []
    for result in results:
        for field, ids in ((result["recipe"], result["ids"]), (result["recipe"] + "-uniform", result["uniform_ids"])):
            annotation = labels[result["case"]][field]
            assert annotation["ids"] == ids, "Visual labels belong to different selected images"
            judgments, count = annotation["labels"], len(ids)
            assert len(judgments) == count and set(judgments) <= {"yes", "no", "ambiguous"}
            row = {"case": result["case"], "recipe": field, "returned": count,
                   **{label: judgments.count(label) for label in ("yes", "no", "ambiguous")},
                   "precision_lower": judgments.count("yes") / count if count else None,
                   "precision_upper": (judgments.count("yes") + judgments.count("ambiguous")) / count if count else None}
            summary.append(row)
    write(ROOT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    discover()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["audit", "run", "report"])
    args = parser.parse_args()
    if args.mode == "report":
        report()
    else:
        app, fixture = setup()
        (audit if args.mode == "audit" else evaluate)(app, fixture)
