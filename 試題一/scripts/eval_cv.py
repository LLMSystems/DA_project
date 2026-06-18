"""以人工標註的 labels.csv 為 ground truth，比較多種 OpenCV 前處理對 ddddocr 辨識率的影響。

用法：
    python scripts/eval_cv.py --dir data/captcha_samples

流程：
1. 讀 labels.csv（filename,label，label 為大寫 ground truth）。
2. 對每種前處理變體，重新編碼成 PNG 丟給 ddddocr，計算：
   - exact：整串完全正確率（大小寫不敏感）
   - char ：字元級正確率（逐位比對，長度不同的多餘/缺漏算錯）
   - len5 ：輸出剛好 5 碼的比例（本站固定 5 碼，可當健康度指標）
3. 依 exact 由高到低列出，找出是否有前處理優於原圖。

注意：ddddocr 對這類 captcha 已相當強，CV 前處理常常無益甚至扣分；
是否採用一律以本表的客觀數字為準。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np


def _read_gray(path: Path) -> np.ndarray:
    data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    return img


def _to_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG 編碼失敗")
    return buf.tobytes()


# --- 前處理變體：輸入灰階圖，輸出處理後的圖（單通道或三通道皆可）---

def v_raw(path: Path) -> bytes:
    return path.read_bytes()


def v_gray(path: Path) -> bytes:
    return _to_png(_read_gray(path))


def v_otsu(path: Path) -> bytes:
    g = _read_gray(path)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _to_png(b)


def v_otsu_median(path: Path) -> bytes:
    g = _read_gray(path)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    b = cv2.medianBlur(b, 3)
    return _to_png(b)


def v_median(path: Path) -> bytes:
    g = _read_gray(path)
    return _to_png(cv2.medianBlur(g, 3))


def v_adaptive(path: Path) -> bytes:
    g = _read_gray(path)
    b = cv2.adaptiveThreshold(
        g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 8
    )
    return _to_png(b)


def v_otsu_open(path: Path) -> bytes:
    # Otsu 後用形態學 open 去細斜線/點雜訊。
    g = _read_gray(path)
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2, 2))
    b = cv2.morphologyEx(b, cv2.MORPH_OPEN, kernel)
    return _to_png(cv2.bitwise_not(b))


def v_upscale2(path: Path) -> bytes:
    g = _read_gray(path)
    up = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return _to_png(up)


def v_upscale_otsu(path: Path) -> bytes:
    g = _read_gray(path)
    up = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, b = cv2.threshold(up, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _to_png(b)


def v_bilateral_otsu(path: Path) -> bytes:
    g = _read_gray(path)
    f = cv2.bilateralFilter(g, 5, 50, 50)
    _, b = cv2.threshold(f, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return _to_png(b)


VARIANTS = {
    "raw": v_raw,
    "gray": v_gray,
    "otsu": v_otsu,
    "otsu+median": v_otsu_median,
    "median": v_median,
    "adaptive": v_adaptive,
    "otsu+open": v_otsu_open,
    "upscale2": v_upscale2,
    "upscale+otsu": v_upscale_otsu,
    "bilateral+otsu": v_bilateral_otsu,
}


def _char_acc(pred: str, truth: str) -> tuple[int, int]:
    """回傳 (對的字元數, ground truth 總字元數)。長度不同：多餘/缺漏皆算錯。"""
    correct = sum(1 for a, b in zip(pred, truth) if a == b)
    return correct, len(truth)


def main() -> int:
    parser = argparse.ArgumentParser(description="比較 CV 前處理對 ddddocr 辨識率的影響")
    parser.add_argument("--dir", type=Path, default=Path("data/captcha_samples"))
    parser.add_argument("--labels", type=Path, default=None, help="ground truth CSV，預設 <dir>/labels.csv")
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

    items = [(args.dir / name, label) for name, label in truth.items() if (args.dir / name).exists()]
    if not items:
        print("[ERROR] labels.csv 對應不到任何圖片", file=sys.stderr)
        return 2

    try:
        import ddddocr  # noqa: PLC0415
    except ImportError:
        print("[ERROR] 未安裝 ddddocr：pip install ddddocr", file=sys.stderr)
        return 2
    ocr = ddddocr.DdddOcr(show_ad=False)

    print(f"ground truth：{len(items)} 張（全大寫比對，大小寫不敏感）\n")
    results = []
    for name, fn in VARIANTS.items():
        exact = 0
        char_ok = 0
        char_tot = 0
        len5 = 0
        for path, label in items:
            try:
                pred = (ocr.classification(fn(path)) or "").strip().upper()
            except Exception:
                pred = ""
            if pred == label:
                exact += 1
            c_ok, c_tot = _char_acc(pred, label)
            char_ok += c_ok
            char_tot += c_tot
            if len(pred) == 5:
                len5 += 1
        results.append(
            (
                name,
                exact / len(items),
                char_ok / char_tot,
                len5 / len(items),
            )
        )

    results.sort(key=lambda r: r[1], reverse=True)
    print(f"{'變體':<16}{'exact':>9}{'char':>9}{'len5':>9}")
    print("-" * 43)
    for name, exact, char, len5 in results:
        print(f"{name:<16}{exact:>8.1%}{char:>9.1%}{len5:>9.1%}")
    print("\n（exact=整串正確率；char=字元級正確率；len5=輸出 5 碼比例）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
