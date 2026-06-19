"""Evaluate captcha variant selector strategies against labeled samples.

This script reuses the production captcha variant generator and caches OCR
outputs so selector experiments can be rerun without repeatedly calling ddddocr.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from doorplate_scraper.scraper import DoorplateScraper


DEFAULT_DIRS = [
    Path("data/captcha_samples"),
    Path("data/captcha_holdout_100"),
    Path("data/captcha_holdout_extra_100"),
]


@dataclass(frozen=True)
class Candidate:
    variant: str
    text: str
    confidence: float


@dataclass(frozen=True)
class Sample:
    dataset: str
    filename: str
    label: str
    candidates: tuple[Candidate, ...]


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
    return f"{path.resolve()}|{stat.st_size}|{int(stat.st_mtime)}|v{variant_count}"


def _read_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _predict_candidates(path: Path, variant_count: int, ocr) -> list[Candidate]:
    png = path.read_bytes()
    candidates: list[Candidate] = []
    for variant, processed in DoorplateScraper._captcha_variant_pngs(png, variant_count):
        result = ocr.classification(processed, probability=True)
        candidates.append(
            Candidate(
                variant=variant,
                text=(result.get("text") or "").strip().upper(),
                confidence=float(result.get("confidence") or 0.0),
            )
        )
    return candidates


def load_samples(
    sample_dirs: Iterable[Path],
    *,
    variant_count: int,
    cache_path: Path,
    refresh: bool,
) -> list[Sample]:
    cache = {} if refresh else _read_cache(cache_path)
    changed = False
    ocr = None
    samples: list[Sample] = []

    for sample_dir in sample_dirs:
        for path, label in _load_labels(sample_dir):
            key = _sample_key(path, variant_count)
            cached = cache.get(key)
            if cached is None:
                if ocr is None:
                    import ddddocr  # noqa: PLC0415

                    ocr = ddddocr.DdddOcr(show_ad=False)
                candidates = _predict_candidates(path, variant_count, ocr)
                cache[key] = {
                    "dataset": sample_dir.name,
                    "filename": path.name,
                    "label": label,
                    "candidates": [candidate.__dict__ for candidate in candidates],
                }
                changed = True
            else:
                candidates = [
                    Candidate(
                        variant=item["variant"],
                        text=item["text"],
                        confidence=float(item["confidence"]),
                    )
                    for item in cached["candidates"]
                ]
            samples.append(
                Sample(
                    dataset=sample_dir.name,
                    filename=path.name,
                    label=label,
                    candidates=tuple(candidates),
                )
            )

    if changed:
        _write_cache(cache_path, cache)
    return samples


def select_current(sample: Sample) -> str:
    return max(sample.candidates, key=lambda item: (len(item.text) == 5, item.confidence)).text


def select_confidence_only(sample: Sample) -> str:
    return max(sample.candidates, key=lambda item: item.confidence).text


def select_text_agreement(sample: Sample) -> str:
    grouped: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in sample.candidates:
        grouped[candidate.text].append(candidate)

    def score(item: tuple[str, list[Candidate]]) -> tuple[bool, float, float]:
        text, members = item
        return (
            len(text) == 5,
            sum(member.confidence for member in members),
            max(member.confidence for member in members),
        )

    return max(grouped.items(), key=score)[0]


def select_weighted_vote(sample: Sample) -> str:
    len5 = [candidate for candidate in sample.candidates if len(candidate.text) == 5]
    if not len5:
        return select_current(sample)

    chars: list[str] = []
    for index in range(5):
        weights: dict[str, float] = defaultdict(float)
        for candidate in len5:
            weights[candidate.text[index]] += candidate.confidence
        chars.append(max(weights.items(), key=lambda item: item[1])[0])
    return "".join(chars)


def variant_exact_rates(samples: Iterable[Sample]) -> dict[str, float]:
    stats: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for sample in samples:
        for candidate in sample.candidates:
            stats[candidate.variant][0] += int(candidate.text == sample.label)
            stats[candidate.variant][1] += 1
    return {
        variant: (correct + 1) / (total + 2)
        for variant, (correct, total) in stats.items()
    }


def make_variant_prior_selector(
    priors: dict[str, float],
    *,
    prior_weight: float,
    confidence_weight: float,
    agreement_weight: float,
) -> Callable[[Sample], str]:
    def select(sample: Sample) -> str:
        text_support: dict[str, float] = defaultdict(float)
        for candidate in sample.candidates:
            text_support[candidate.text] += candidate.confidence

        def score(candidate: Candidate) -> tuple[bool, float]:
            return (
                len(candidate.text) == 5,
                confidence_weight * candidate.confidence
                + prior_weight * priors.get(candidate.variant, 0.0)
                + agreement_weight * text_support[candidate.text],
            )

        return max(sample.candidates, key=score).text

    return select


def accuracy(samples: Iterable[Sample], selector: Callable[[Sample], str]) -> tuple[int, int]:
    items = list(samples)
    return sum(1 for sample in items if selector(sample) == sample.label), len(items)


def char_accuracy(samples: Iterable[Sample], selector: Callable[[Sample], str]) -> tuple[int, int]:
    ok = 0
    total = 0
    for sample in samples:
        pred = selector(sample)
        ok += sum(1 for a, b in zip(pred, sample.label) if a == b)
        total += max(len(pred), len(sample.label))
    return ok, total


def print_result(name: str, samples: list[Sample], selector: Callable[[Sample], str]) -> None:
    exact, total = accuracy(samples, selector)
    char_ok, char_total = char_accuracy(samples, selector)
    len5 = sum(1 for sample in samples if len(selector(sample)) == 5)
    print(
        f"{name:<28} exact={exact:>3}/{total:<3} ({exact / total:>5.1%}) "
        f"char={char_ok:>4}/{char_total:<4} ({char_ok / char_total:>5.1%}) "
        f"len5={len5:>3}/{total:<3} ({len5 / total:>5.1%})"
    )


def dataset_splits(samples: list[Sample]) -> dict[str, list[Sample]]:
    splits: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        splits[sample.dataset].append(sample)
    splits["combined_holdout_200"] = [
        sample for sample in samples if sample.dataset != "captcha_samples"
    ]
    splits["all_300"] = samples
    return dict(splits)


def tune_prior_selector(train: list[Sample]) -> tuple[str, Callable[[Sample], str]]:
    priors = variant_exact_rates(train)
    best: tuple[int, str, Callable[[Sample], str]] | None = None

    for prior_weight in [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
        for confidence_weight in [0.25, 0.5, 1.0, 1.5, 2.0]:
            for agreement_weight in [0.0, 0.1, 0.25, 0.5, 1.0]:
                selector = make_variant_prior_selector(
                    priors,
                    prior_weight=prior_weight,
                    confidence_weight=confidence_weight,
                    agreement_weight=agreement_weight,
                )
                exact, _ = accuracy(train, selector)
                label = (
                    "prior("
                    f"p={prior_weight:g},c={confidence_weight:g},a={agreement_weight:g}"
                    ")"
                )
                if best is None or exact > best[0]:
                    best = (exact, label, selector)

    assert best is not None
    return best[1], best[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dirs", nargs="*", type=Path, default=DEFAULT_DIRS)
    parser.add_argument("--variants", type=int, default=18)
    parser.add_argument("--cache", type=Path, default=Path("data/captcha_variant_predictions.json"))
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
    print(f"samples={len(samples)} variants={args.variants} cache={args.cache}")
    print()

    splits = dataset_splits(samples)
    baseline_selectors: list[tuple[str, Callable[[Sample], str]]] = [
        ("current_len5_conf", select_current),
        ("confidence_only", select_confidence_only),
        ("text_agreement", select_text_agreement),
        ("weighted_vote", select_weighted_vote),
    ]

    for split_name, split_samples in splits.items():
        print(f"[{split_name}]")
        for name, selector in baseline_selectors:
            print_result(name, split_samples, selector)
        print()

    train_old = splits.get("captcha_samples", [])
    if train_old:
        tuned_name, tuned_selector = tune_prior_selector(train_old)
        print(f"[variant prior tuned on captcha_samples] {tuned_name}")
        for split_name in ["captcha_samples", "captcha_holdout_100", "captcha_holdout_extra_100", "combined_holdout_200"]:
            if split_name in splits:
                print_result(split_name, splits[split_name], tuned_selector)
        print()

    train_old_holdout1 = splits.get("captcha_samples", []) + splits.get("captcha_holdout_100", [])
    if train_old_holdout1:
        tuned_name, tuned_selector = tune_prior_selector(train_old_holdout1)
        print(f"[variant prior tuned on old100 + holdout#1] {tuned_name}")
        for split_name in ["captcha_holdout_extra_100", "combined_holdout_200", "all_300"]:
            if split_name in splits:
                print_result(split_name, splits[split_name], tuned_selector)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
