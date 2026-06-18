"""用 ddddocr 評測驗證碼辨識率：對一批圖片做預測，輸出可人工核對的報告。

用法：
    python scripts/eval_captcha.py --dir data/captcha_samples

產出：
- <dir>/eval_predictions.csv ：每張圖的檔名與預測字串。
- <dir>/eval_report.html      ：每張圖內嵌縮圖 + 預測，方便一路掃下去人工核對。

驗證碼沒有現成標籤，因此正確率需人工檢視 HTML 報告計數；報告底部有
長度分佈等統計，並提供「複製清單」方便標記錯誤項。
"""
from __future__ import annotations

import argparse
import base64
import sys
import time
from collections import Counter
from html import escape
from pathlib import Path


def _load_ocr():
    try:
        import ddddocr  # noqa: PLC0415
    except ImportError:
        print("[ERROR] 尚未安裝 ddddocr，請先執行：pip install ddddocr", file=sys.stderr)
        raise SystemExit(2)
    # show_ad=False 關閉啟動時的廣告輸出；beta=False 用預設模型。
    return ddddocr.DdddOcr(show_ad=False)


def _render_html(rows: list[tuple[str, str, str]], length_hist: Counter) -> str:
    cells = []
    for name, b64, pred in rows:
        cells.append(
            f"""
            <div class="cell">
              <img src="data:image/png;base64,{b64}" alt="{escape(name)}"/>
              <div class="pred">{escape(pred) or "<空>"}</div>
              <div class="name">{escape(name)}</div>
            </div>
            """
        )
    hist = "  ".join(f"{length}碼×{count}" for length, count in sorted(length_hist.items()))
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<title>驗證碼 ddddocr 評測報告</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f6f7f9; }}
  h1 {{ font-size: 20px; }}
  .summary {{ margin-bottom: 16px; color: #333; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }}
  .cell {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 8px; text-align: center; }}
  .cell img {{ width: 100%; image-rendering: pixelated; background: #fff; }}
  .pred {{ font-size: 20px; font-weight: 700; letter-spacing: 2px; margin-top: 6px; }}
  .name {{ font-size: 11px; color: #999; }}
</style>
</head>
<body>
  <h1>驗證碼 ddddocr 評測報告</h1>
  <div class="summary">
    共 {len(rows)} 張。長度分佈：{hist}。<br/>
    人工核對方式：逐格比對圖片與下方粗體預測，數出錯誤張數即為錯誤率。
  </div>
  <div class="grid">
    {''.join(cells)}
  </div>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="用 ddddocr 評測驗證碼辨識率")
    parser.add_argument("--dir", type=Path, default=Path("data/captcha_samples"), help="圖片資料夾")
    parser.add_argument("--glob", default="*.png", help="圖片檔名樣式")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    if not args.dir.exists():
        print(f"[ERROR] 資料夾不存在：{args.dir}", file=sys.stderr)
        return 2

    images = sorted(args.dir.glob(args.glob))
    if not images:
        print(f"[ERROR] {args.dir} 下找不到符合 {args.glob} 的圖片", file=sys.stderr)
        return 2

    ocr = _load_ocr()

    rows: list[tuple[str, str, str]] = []
    length_hist: Counter = Counter()
    csv_lines = ["filename,prediction"]
    start = time.perf_counter()
    for path in images:
        data = path.read_bytes()
        pred = (ocr.classification(data) or "").strip()
        b64 = base64.b64encode(data).decode("ascii")
        rows.append((path.name, b64, pred))
        length_hist[len(pred)] += 1
        csv_lines.append(f"{path.name},{pred}")
    elapsed = time.perf_counter() - start

    csv_path = args.dir / "eval_predictions.csv"
    html_path = args.dir / "eval_report.html"
    csv_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
    html_path.write_text(_render_html(rows, length_hist), encoding="utf-8")

    print(f"圖片數      : {len(images)}")
    print(f"總耗時      : {elapsed:.2f}s（平均 {elapsed / len(images) * 1000:.1f} ms/張）")
    print(f"長度分佈    : {dict(sorted(length_hist.items()))}")
    print(f"預測 CSV    : {csv_path}")
    print(f"核對報告    : {html_path}")
    print("→ 用瀏覽器開啟核對報告，逐格比對圖片與預測，數出錯誤張數即為錯誤率。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
