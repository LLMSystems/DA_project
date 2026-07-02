"""Debug a single captcha image through OCR probability output and beam search.

Example:
    .\.venv\Scripts\python .\試題一\scripts\demo_beam_search.py `
        .\試題一\data\captcha_holdout_100\captcha_0001.png `
        --variants 18 `
        --save-dir .\artifacts\beam_debug

This script is intentionally verbose so it can be used while debugging:
1. Load one captcha image.
2. Generate the same preprocessing variants used in production.
3. Run ddddocr with probability=True on each variant.
4. Print the native OCR output and the custom beam-search output.
5. Print the top-k character probabilities for every timestep.
6. Show which candidate would be selected by the production selectors.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doorplate_scraper.config import CrawlerConfig
from doorplate_scraper.scraper import CaptchaCandidate, DoorplateScraper


def _step_topk(result: dict, top_k: int) -> list[list[tuple[str, float]]]:
    probabilities = result.get("probabilities") or []
    charset = result.get("charset") or []
    if not probabilities or not charset:
        return []

    rows: list[list[tuple[str, float]]] = []
    for step in probabilities:
        row = step[0]
        items = []
        for index, prob in enumerate(row):
            char = "<blank>" if index == 0 else str(charset[index])
            items.append((char, float(prob)))
        items.sort(key=lambda item: item[1], reverse=True)
        rows.append(items[:top_k])
    return rows


def _format_topk(rows: list[list[tuple[str, float]]]) -> str:
    lines: list[str] = []
    for index, top_items in enumerate(rows, start=1):
        parts = [f"{char}:{prob:.3f}" for char, prob in top_items]
        lines.append(f"  t{index:02d}: " + " | ".join(parts))
    return "\n".join(lines)


def _select_native(candidates: list[CaptchaCandidate], captcha_length: int) -> CaptchaCandidate:
    return max(
        candidates,
        key=lambda item: (len(item.text) == captcha_length, item.confidence),
    )


def _save_variants(
    *,
    save_dir: Path,
    original_png: bytes,
    variants: list[tuple[str, bytes]],
) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "original.png").write_bytes(original_png)
    for index, (name, png) in enumerate(variants, start=1):
        path = save_dir / f"{index:02d}_{name}.png"
        path.write_bytes(png)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="captcha image path")
    parser.add_argument("--variants", type=int, default=18, help="number of preprocessing variants")
    parser.add_argument("--top-k", type=int, default=5, help="top probabilities to print per timestep")
    parser.add_argument(
        "--save-dir",
        type=Path,
        default=None,
        help="optional directory to save original/processed PNGs for visual diff",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    if not args.image.exists():
        print(f"[ERROR] image not found: {args.image}", file=sys.stderr)
        return 2

    try:
        import ddddocr  # noqa: PLC0415
    except ImportError:
        print("[ERROR] ddddocr not installed. pip install ddddocr", file=sys.stderr)
        return 2

    config = CrawlerConfig(captcha_variant_count=max(1, args.variants), captcha_decoder="beam")
    scraper = DoorplateScraper(config, logger=logging.getLogger("beam-demo"))
    ocr = ddddocr.DdddOcr(show_ad=False)

    original_png = args.image.read_bytes()
    if config.captcha_variant_count == 1:
        variants = [("otsu", DoorplateScraper._otsu_png(original_png))]
    else:
        variants = DoorplateScraper._captcha_variant_pngs(original_png, config.captcha_variant_count)

    if args.save_dir is not None:
        _save_variants(save_dir=args.save_dir, original_png=original_png, variants=variants)

    print(f"image      : {args.image.resolve()}")
    print(f"variants   : {len(variants)}")
    print(f"captcha_len: {config.captcha_length}")
    print()

    native_candidates: list[CaptchaCandidate] = []
    beam_candidates: list[CaptchaCandidate] = []

    for index, (variant_name, processed) in enumerate(variants, start=1):
        result = ocr.classification(processed, probability=True)
        native_text = (result.get("text") or "").strip().upper()
        native_conf = float(result.get("confidence") or 0.0)
        beam_text, beam_conf = scraper._ctc_beam_decode(result, config.captcha_length)
        native_candidates.append(CaptchaCandidate(variant_name, native_text, native_conf))
        beam_candidates.append(CaptchaCandidate(variant_name, beam_text, beam_conf))

        print(f"[variant {index:02d}] {variant_name}")
        print(
            "  native => "
            f"text={native_text!r} len={len(native_text)} conf={native_conf:.4f}"
        )
        print(
            "  beam   => "
            f"text={beam_text!r} len={len(beam_text)} conf={beam_conf:.4f}"
        )

        topk_rows = _step_topk(result, args.top_k)
        if topk_rows:
            print("  top-k probabilities")
            print(_format_topk(topk_rows))
        else:
            print("  top-k probabilities: <not available>")
        print()

    selected_native = _select_native(native_candidates, config.captcha_length)
    selected_beam = scraper._select_captcha_by_agreement(beam_candidates)

    print("[production selector summary]")
    print(
        "  native selector => "
        f"{selected_native.variant_name} text={selected_native.text!r} "
        f"len={len(selected_native.text)} conf={selected_native.confidence:.4f}"
    )
    print(
        "  beam selector   => "
        f"{selected_beam.variant_name} text={selected_beam.text!r} "
        f"len={len(selected_beam.text)} conf={selected_beam.confidence:.4f}"
    )

    if args.save_dir is not None:
        print()
        print(f"saved processed PNGs under: {args.save_dir.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
