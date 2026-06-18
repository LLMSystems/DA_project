"""在 otsu 前處理之上，疊加 ddddocr 字元範圍限制(set_ranges)，比較辨識率。

用法：
    python scripts/eval_cv_ranges.py --dir data/captcha_samples

模式：
- otsu            ：otsu 二值化 + 預設解碼（無範圍限制）
- otsu+range5     ：otsu + 限制字集為 A-Z 與 0-9（安全，正式可用）
- otsu+observed   ：otsu + 限制為 labels 實際出現過的字集（最激進，有未見字被強制誤判風險）
- raw+range5      ：原圖 + A-Z 0-9（對照，檢查 otsu 是否仍必要）

字元範圍限制需用 classification(probability=True) 取機率矩陣，再於限制字集內取 argmax。

結論（重要）：本機 ddddocr 1.6.1 的 set_ranges 對自訂字集已失效（charset 仍為全集、
probabilities 每步僅回傳所選字的信心值而非全字集分佈），故無法自行做限制字集解碼，
所有 range 模式皆為 0%。此腳本保留作為「字集限制此路不通」的佐證；
最終採用的是 eval_cv.py 驗證出的 otsu 前處理 + 5 碼閘門。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np

UPPER_DIGITS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _read_gray(path: Path) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def _to_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG 編碼失敗")
    return buf.tobytes()


def _otsu_png(path: Path) -> bytes:
    g = _read_gray(path)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _to_png(b)


def _decode_default(ocr, img_bytes: bytes) -> str:
    return (ocr.classification(img_bytes) or "").strip().upper()


def _decode_ranges(ocr, img_bytes: bytes, charset: str) -> str:
    # set_ranges 後，classification(probability=True) 的 'text' 即為限制字集後的解碼結果。
    ocr.set_ranges(charset)
    res = ocr.classification(img_bytes, probability=True)
    return (res["text"] or "").strip().upper()


def main() -> int:
    parser = argparse.ArgumentParser(description="otsu + ddddocr 字元範圍限制評測")
    parser.add_argument("--dir", type=Path, default=Path("data/captcha_samples"))
    parser.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    labels_path = args.labels or (args.dir / "labels.csv")
    if not labels_path.exists():
        print(f"[ERROR] 找不到 ground truth：{labels_path}", file=sys.stderr)
        return 2

    truth: dict[str, str] = {}
    with labels_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            label = (row.get("label") or "").strip().upper()
            if label:
                truth[row["filename"]] = label

    items = [(args.dir / n, lab) for n, lab in truth.items() if (args.dir / n).exists()]
    if not items:
        print("[ERROR] labels.csv 對應不到任何圖片", file=sys.stderr)
        return 2

    observed = "".join(sorted({c for _, lab in items for c in lab}))
    print(f"ground truth：{len(items)} 張")
    print(f"觀察到的字集（{len(observed)} 種）：{observed}\n")

    try:
        import ddddocr  # noqa: PLC0415
    except ImportError:
        print("[ERROR] 未安裝 ddddocr：pip install ddddocr", file=sys.stderr)
        return 2
    ocr = ddddocr.DdddOcr(show_ad=False)

    modes = [
        ("otsu", lambda p: _decode_default(ocr, _otsu_png(p))),
        ("otsu+range5", lambda p: _decode_ranges(ocr, _otsu_png(p), UPPER_DIGITS)),
        ("otsu+observed", lambda p: _decode_ranges(ocr, _otsu_png(p), observed)),
        ("raw+range5", lambda p: _decode_ranges(ocr, p.read_bytes(), UPPER_DIGITS)),
    ]

    results = []
    for name, fn in modes:
        exact = char_ok = char_tot = len5 = exact5 = n5 = 0
        for path, label in items:
            try:
                pred = fn(path)
            except Exception as exc:  # noqa: BLE001
                pred = ""
                print(f"[WARN] {name} {path.name}: {exc}", file=sys.stderr)
            if pred == label:
                exact += 1
            char_ok += sum(1 for a, b in zip(pred, label) if a == b)
            char_tot += len(label)
            if len(pred) == 5:
                len5 += 1
                n5 += 1
                if pred == label:
                    exact5 += 1
        results.append(
            (
                name,
                exact / len(items),
                char_ok / char_tot,
                len5 / len(items),
                (exact5 / n5) if n5 else 0.0,
            )
        )

    print(f"{'模式':<16}{'exact':>9}{'char':>9}{'len5':>9}{'exact|5碼':>11}")
    print("-" * 54)
    for name, exact, char, len5, exact5 in results:
        print(f"{name:<16}{exact:>8.1%}{char:>9.1%}{len5:>9.1%}{exact5:>11.1%}")
    print("\n（exact=整串正確；char=字元級；len5=輸出5碼比例；exact|5碼=只看輸出5碼者的整串正確率）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
