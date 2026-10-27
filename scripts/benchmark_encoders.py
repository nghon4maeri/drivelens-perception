"""CPU benchmark of pinned full versus split image/text encoders, no production configuration.

Run --variant clip_full|clip_text|siglip_full|siglip_text|siglip_vision --output output/encoder-bench.
Downloads only public pinned checkpoints into explicit project cache. Synthetic images test numerical parity,
not semantic quality on ADAS. Gemma4 is evaluated as a generation model in the research report, not downloaded here.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

CLIP = ("openai/clip-vit-base-patch32", "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268")
SIGLIP = ("google/siglip2-base-patch16-224", "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2")
TEXTS = ["a highway with cars", "a night road", "a pedestrian crossing", "a traffic light",
         "đường cao tốc có ô tô", "đường vào ban đêm", "người đi bộ qua đường", "đèn giao thông"]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--variant", choices=("clip_full", "clip_text", "siglip_full", "siglip_text", "siglip_vision"), required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--threads", type=int, default=1)
    args = p.parse_args()
    if args.threads < 1 or not any(args.output.resolve().is_relative_to(Path(x).resolve()) for x in ("data", "output")):
        p.error("output must be under data/output; threads must be positive")
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OMP_NUM_THREADS"] = os.environ["MKL_NUM_THREADS"] = str(args.threads)
    import numpy as np
    import torch
    from PIL import Image
    from transformers import (
        AutoConfig,
        AutoProcessor,
        CLIPModel,
        CLIPTextModelWithProjection,
        SiglipModel,
        SiglipTextModel,
        SiglipVisionModel,
    )

    from src.rav.worker.process import rss_bytes

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    model_id, revision = CLIP if args.variant.startswith("clip") else SIGLIP
    cache = args.output / "hub"
    opts = {"revision": revision, "cache_dir": str(cache)}
    start = time.monotonic()
    config = AutoConfig.from_pretrained(model_id, **opts)
    classes = {"clip_full": CLIPModel, "clip_text": CLIPTextModelWithProjection,
               "siglip_full": SiglipModel, "siglip_text": SiglipTextModel, "siglip_vision": SiglipVisionModel}
    model = classes[args.variant].from_pretrained(model_id, **opts).eval()
    processor = AutoProcessor.from_pretrained(model_id, **opts)
    load_s = time.monotonic() - start
    images = [Image.fromarray(np.full((360, 640, 3), (i*31) % 255, np.uint8)) for i in range(8)]

    def text():
        opts = {"padding": True} if args.variant.startswith("clip") else {"padding": "max_length", "max_length": 64}
        inputs = processor.tokenizer(TEXTS, return_tensors="pt", truncation=False, **opts)
        with torch.inference_mode():
            if args.variant.endswith("full"):
                v = model.get_text_features(**inputs)
            elif args.variant == "clip_text":
                v = model(**inputs).text_embeds
            else:
                v = model(**inputs).pooler_output
        return torch.nn.functional.normalize(v.float(), dim=-1).numpy()

    def vision():
        inputs = processor(images=images, return_tensors="pt")
        with torch.inference_mode():
            v = model.get_image_features(**inputs) if args.variant.endswith("full") else model(**inputs).pooler_output
        return torch.nn.functional.normalize(v.float(), dim=-1).numpy()

    result = {"variant": args.variant, "model": model_id, "revision": revision, "threads": args.threads,
              "load_s_including_download": round(load_s, 3), "parameters": sum(x.numel() for x in model.parameters()),
              "rss_after_load_bytes": rss_bytes(), "scope": "CPU performance + numerical split parity, synthetic images; not semantic uplift"}
    outputs = {}
    funcs = [("vision", vision)] if args.variant.endswith("vision") else [("text", text)]
    if args.variant.endswith("full"):
        funcs.append(("vision", vision))
    for name, fn in funcs:
        start = time.monotonic()
        outputs[name] = fn()
        cold = time.monotonic() - start
        values = []
        for _ in range(7):
            start = time.monotonic()
            fn()
            values.append(time.monotonic() - start)
        result[name] = {"batch": 8, "first_s": round(cold, 4), "warm_p50_s": round(float(np.median(values)), 4),
                        "warm_samples_s": [round(x, 4) for x in values], "shape": list(outputs[name].shape)}
    result["rss_after_inference_bytes"] = rss_bytes()
    result["model_type"] = config.model_type
    stem = f"{args.variant}-t{args.threads}"
    np.savez(args.output / (stem+".npz"), **outputs)
    (args.output / (stem+".json")).write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
