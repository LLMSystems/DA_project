"""Evaluate beam-decoder captcha variants with leave-one-out ablation.

The script caches the expensive OCR + CTC beam decoding step, then recomputes
the production agreement selector for all variants and every leave-one-out set.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doorplate_scraper.config import CrawlerConfig
from doorplate_scraper.scraper import CaptchaCandidate, DoorplateScraper


DEFAULT_DIRS = [
    Path("data/captcha_holdout_100"),
    Path("data/captcha_holdout_extra_100"),
]


def _load_labels(sample_dir: Path) -> list[tuple[Path, str]]:
    labels_path = sample_dir / "labels.csv"
    if not labels_path.exists():
        return []

    items: list[tuple[Path, str]] = []
    with labels_path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            filename = (row.get("filename") or row.get("file") or "").strip()
            label = (row.get("label") or row.get("text") or "").strip().upper()
            path = sample_dir / filename
            if filename and label and path.exists():
                items.append((path, label))
    return items


def _sample_key(path: Path, variant_count: int) -> str:
    stat = path.stat()
    return f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|beam|v{variant_count}"


def _read_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _beam_candidates(path: Path, variant_count: int, scraper: DoorplateScraper, ocr) -> list[CaptchaCandidate]:
    candidates: list[CaptchaCandidate] = []
    for variant_name, processed in DoorplateScraper._captcha_variant_pngs(
        path.read_bytes(), variant_count
    ):
        result = ocr.classification(processed, probability=True)
        text, confidence = scraper._ctc_beam_decode(result, scraper.config.captcha_length)
        candidates.append(CaptchaCandidate(variant_name, text, confidence))
    return candidates


def load_samples(
    sample_dirs: Iterable[Path],
    *,
    variant_count: int,
    cache_path: Path,
    refresh: bool,
) -> list[dict]:
    cache = {} if refresh else _read_cache(cache_path)
    config = CrawlerConfig(captcha_variant_count=variant_count, captcha_decoder="beam")
    scraper = DoorplateScraper(config, logger=logging.getLogger("beam-ablation"))
    ocr = None
    changed = False
    samples: list[dict] = []

    for sample_dir in sample_dirs:
        for path, label in _load_labels(sample_dir):
            key = _sample_key(path, variant_count)
            cached = cache.get(key)
            if cached is None:
                if ocr is None:
                    import ddddocr  # noqa: PLC0415

                    ocr = ddddocr.DdddOcr(show_ad=False)
                candidates = _beam_candidates(path, variant_count, scraper, ocr)
                cached = {
                    "dataset": sample_dir.name,
                    "filename": path.name,
                    "label": label,
                    "candidates": [asdict(candidate) for candidate in candidates],
                }
                cache[key] = cached
                changed = True
            samples.append(cached)

    if changed:
        _write_cache(cache_path, cache)
    return samples


def select_by_agreement(candidates: list[dict], allowed_variants: set[str]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        if candidate["variant_name"] in allowed_variants:
            grouped[candidate["text"]].append(candidate)
    if not grouped:
        raise ValueError("No candidates left after filtering variants")

    text, members = max(
        grouped.items(),
        key=lambda item: (
            len(item[0]) == 5,
            sum(candidate["confidence"] for candidate in item[1]),
            max(candidate["confidence"] for candidate in item[1]),
        ),
    )
    selected = max(members, key=lambda candidate: candidate["confidence"])
    return {**selected, "text": text}


def score(samples: list[dict], variants: list[str]) -> dict:
    allowed = set(variants)
    exact = 0
    char_ok = 0
    char_total = 0
    len5 = 0
    selected = Counter()
    wrong: list[tuple[str, str, str]] = []

    for sample in samples:
        candidate = select_by_agreement(sample["candidates"], allowed)
        pred = candidate["text"]
        truth = sample["label"]
        exact += pred == truth
        len5 += len(pred) == 5
        char_ok += sum(1 for a, b in zip(pred, truth) if a == b)
        char_total += max(len(pred), len(truth))
        selected[candidate["variant_name"]] += 1
        if pred != truth and len(wrong) < 8:
            wrong.append((sample["filename"], truth, pred))

    total = len(samples)
    return {
        "exact": exact,
        "total": total,
        "exact_rate": exact / total,
        "char_ok": char_ok,
        "char_total": char_total,
        "char_rate": char_ok / char_total,
        "len5": len5,
        "len5_rate": len5 / total,
        "selected": selected,
        "wrong": wrong,
    }


def print_score(label: str, result: dict) -> None:
    print(
        f"{label:<26} exact={result['exact']:>3}/{result['total']:<3} "
        f"({result['exact_rate']:>5.1%}) char={result['char_ok']:>4}/{result['char_total']:<4} "
        f"({result['char_rate']:>5.1%}) len5={result['len5']:>3}/{result['total']:<3} "
        f"({result['len5_rate']:>5.1%})"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirs", nargs="*", type=Path, default=DEFAULT_DIRS)
    parser.add_argument("--variants", type=int, default=18)
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path("data/captcha_beam_predictions.json"),
    )
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    samples = load_samples(
        args.dirs,
        variant_count=args.variants,
        cache_path=args.cache,
        refresh=args.refresh,
    )
    if not samples:
        print("[ERROR] no labeled samples found", file=sys.stderr)
        return 2

    variant_names = [
        candidate["variant_name"]
        for candidate in samples[0]["candidates"]
    ]

    print(f"samples={len(samples)} variants={len(variant_names)} cache={args.cache}")
    print()

    full = score(samples, variant_names)
    print_score("all variants", full)
    print()

    rows = []
    for variant in variant_names:
        kept = [name for name in variant_names if name != variant]
        result = score(samples, kept)
        rows.append((result["exact"], result["char_rate"], variant, result))

    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    print("[leave one out]")
    for _, _, removed, result in rows:
        delta = result["exact"] - full["exact"]
        print_score(f"drop {removed}", result)
        print(f"  delta_exact={delta:+d}")

    best = rows[0]
    if best[0] > full["exact"]:
        print()
        print(
            f"best improvement: drop {best[2]} "
            f"({full['exact']}/{full['total']} -> {best[3]['exact']}/{best[3]['total']})"
        )
    else:
        print()
        print("no single dropped variant improved exact accuracy")

    print()
    print("[selected variant counts: all variants]")
    for variant, count in full["selected"].most_common():
        print(f"{variant:<20} {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
