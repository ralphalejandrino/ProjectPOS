#!/usr/bin/env python3
"""Batch-generate consistent POS product images with Nano Banana Pro (Gemini 3
Pro Image), using per-product / per-category reference photos.

Run this on a machine with internet + a Gemini API key (NOT the POS box). It
reads a products manifest (slug,name,category), finds the most specific
reference image for each product, and writes one square PNG per product to the
output dir, named ``<slug>.png`` so pos.import_product_photos can match it
back to the Item by re-slugifying Item.name.

  pip install "google-genai>=0.3" pillow
  export GEMINI_API_KEY=...                  # or GOOGLE_API_KEY
  python generate_product_images.py \
      --manifest products.csv \
      --references ./references \
      --out ./out

Reference lookup per product (first hit wins, all matches in a dir are sent):
  references/<slug>.{png,jpg,jpeg,webp}              product-specific
  references/_category/<category-slug>.{...}         category fallback
  references/_style/*.{...}                          global style anchors (always added)

Manifest CSV must have a header with at least: slug,name,category
(generate it from the box — see the command blocks in chat.)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    sys.exit("Missing dependency: pip install 'google-genai>=0.3' pillow")

# Nano Banana Pro. Override with --model if Google renames the endpoint.
DEFAULT_MODEL = "gemini-3-pro-image-preview"
IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# The consistency contract: identical framing/background/lighting for every
# product so the POS grid looks like one coherent set. The per-product
# reference supplies the actual drink likeness; this preamble pins the style.
STYLE = (
    "Professional studio product photograph for a point-of-sale menu tile. "
    "A single serving of the drink/food centered in frame, shot straight-on at "
    "a slight 15-degree downward angle. Clean seamless soft-cream (#F5F0E8) "
    "background, no props, no text, no logos, no watermarks, no hands. Soft "
    "diffused studio lighting from upper-left, gentle natural shadow beneath. "
    "Vibrant true-to-life colors, crisp focus, appetizing and fresh. Square 1:1 "
    "composition with even margins. Match the serving style, cup/glass, and "
    "garnish shown in the reference image(s); keep the look consistent with the "
    "rest of the menu set."
)


def slugify(value: str) -> str:
    """Mirror django.utils.text.slugify so slugs match the import side exactly."""
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE).strip().lower()
    return re.sub(r"[-\s]+", "-", value, flags=re.UNICODE)


def _imgs_in(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXTS)


def _named(d: Path, stem: str) -> list[Path]:
    return [p for ext in IMG_EXTS for p in [d / f"{stem}{ext}"] if p.exists()]


def references_for(slug: str, category: str, refroot: Path) -> list[Path]:
    """Most-specific-first: product slug, else category, plus global style anchors."""
    refs = _named(refroot, slug)
    if not refs and category:
        refs = _named(refroot / "_category", slugify(category))
    refs += _imgs_in(refroot / "_style")
    return refs


def _load_part(path: Path) -> types.Part:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)


def generate_one(client, model, name, refs) -> bytes | None:
    prompt = f'{STYLE}\n\nProduct: "{name}".'
    contents = [prompt] + [_load_part(p) for p in refs]
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_modalities=["Image"],
            image_config=types.ImageConfig(aspect_ratio="1:1"),
        ),
    )
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else []):
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, type=Path, help="CSV: slug,name,category")
    ap.add_argument("--references", required=True, type=Path, help="reference image dir")
    ap.add_argument("--out", required=True, type=Path, help="output dir for <slug>.png")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--overwrite", action="store_true", help="regenerate even if <slug>.png exists")
    ap.add_argument("--only", default="", help="comma-separated slugs to (re)generate")
    ap.add_argument("--sleep", type=float, default=2.0, help="seconds between API calls")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        sys.exit("Set GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment.")

    client = genai.Client(api_key=api_key)
    args.out.mkdir(parents=True, exist_ok=True)
    only = {s.strip() for s in args.only.split(",") if s.strip()}

    with args.manifest.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "name" not in rows[0]:
        sys.exit("Manifest needs a header with at least: slug,name,category")

    ok = skipped = failed = 0
    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue
        slug = (row.get("slug") or "").strip() or slugify(name)
        category = (row.get("category") or "").strip()
        if only and slug not in only:
            continue

        dest = args.out / f"{slug}.png"
        if dest.exists() and not args.overwrite:
            print(f"  skip (exists)   {slug}")
            skipped += 1
            continue

        refs = references_for(slug, category, args.references)
        if not refs:
            print(f"  ⚠ NO REFERENCE  {slug}  ({name}) — skipped")
            skipped += 1
            continue

        try:
            data = generate_one(client, args.model, name, refs)
            if not data:
                print(f"  ✗ no image     {slug} (model returned no image part)")
                failed += 1
            else:
                dest.write_bytes(data)
                print(f"  ✓ {slug:<40} <- {', '.join(p.name for p in refs)}")
                ok += 1
        except Exception as e:  # noqa: BLE001 — keep batching past one bad item
            print(f"  ✗ ERROR        {slug}: {e}")
            failed += 1
        time.sleep(args.sleep)

    print(f"\nDone. generated={ok} skipped={skipped} failed={failed} -> {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
