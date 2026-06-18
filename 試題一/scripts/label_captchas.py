"""產生驗證碼標註頁(HTML)：人工標 ground truth，匯出 labels.csv。

用法：
    python scripts/label_captchas.py --dir data/captcha_samples

說明：
- 每張圖的輸入框會預先填入 ddddocr 預測（轉大寫），多數已正確，只要改錯的幾張。
- 在瀏覽器開啟產生的 label_tool.html，逐格修正後按「下載 labels.csv」。
- 把下載的 labels.csv 放回圖片資料夾，即可當作 ground truth 給評測腳本使用。
- 預填來源：同資料夾的 eval_predictions.csv（若無則即時呼叫 ddddocr）。
"""
from __future__ import annotations

import argparse
import base64
import csv
import sys
from html import escape
from pathlib import Path


def _load_predictions(pred_csv: Path) -> dict[str, str]:
    preds: dict[str, str] = {}
    if pred_csv.exists():
        with pred_csv.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                preds[row["filename"]] = row.get("prediction", "")
    return preds


def _predict_missing(images: list[Path], preds: dict[str, str]) -> dict[str, str]:
    missing = [p for p in images if p.name not in preds]
    if not missing:
        return preds
    try:
        import ddddocr  # noqa: PLC0415
    except ImportError:
        print("[WARN] 無 eval_predictions.csv 且未安裝 ddddocr，輸入框將留空。", file=sys.stderr)
        return preds
    ocr = ddddocr.DdddOcr(show_ad=False)
    for path in missing:
        preds[path.name] = (ocr.classification(path.read_bytes()) or "").strip()
    return preds


def _render_html(rows: list[tuple[str, str, str]]) -> str:
    cells = []
    for idx, (name, b64, prefill) in enumerate(rows):
        cells.append(
            f"""
            <div class="cell">
              <img src="data:image/png;base64,{b64}" alt="{escape(name)}"/>
              <input type="text" data-name="{escape(name)}" value="{escape(prefill)}"
                     autocomplete="off" autocapitalize="characters" spellcheck="false"
                     tabindex="{idx + 1}"/>
              <div class="name">{escape(name)}</div>
            </div>
            """
        )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"/>
<title>驗證碼標註工具</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 24px; background: #f6f7f9; }}
  h1 {{ font-size: 20px; }}
  .bar {{ position: sticky; top: 0; background: #f6f7f9; padding: 10px 0; z-index: 10; }}
  button {{ font-size: 15px; padding: 8px 16px; cursor: pointer; }}
  #status {{ margin-left: 12px; color: #333; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }}
  .cell {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 8px; text-align: center; }}
  .cell img {{ width: 100%; image-rendering: pixelated; background: #fff; }}
  .cell input {{ width: 90%; margin-top: 6px; font-size: 18px; text-align: center;
                 text-transform: uppercase; letter-spacing: 2px; }}
  .name {{ font-size: 11px; color: #999; margin-top: 4px; }}
</style>
</head>
<body>
  <h1>驗證碼標註工具</h1>
  <div class="bar">
    <button onclick="downloadCsv()">下載 labels.csv</button>
    <span id="status"></span>
  </div>
  <div class="grid">
    {''.join(cells)}
  </div>
<script>
  function downloadCsv() {{
    const inputs = document.querySelectorAll('input[data-name]');
    let lines = ['filename,label'];
    let blank = 0;
    inputs.forEach(inp => {{
      const v = inp.value.trim().toUpperCase();
      if (!v) blank++;
      lines.push(inp.dataset.name + ',' + v);
    }});
    const blob = new Blob([lines.join('\\n') + '\\n'], {{ type: 'text/csv' }});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'labels.csv';
    a.click();
    document.getElementById('status').textContent =
      '已匯出 ' + inputs.length + ' 筆（空白 ' + blank + ' 筆）。請把 labels.csv 放回圖片資料夾。';
  }}
</script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="產生驗證碼標註頁")
    parser.add_argument("--dir", type=Path, default=Path("data/captcha_samples"), help="圖片資料夾")
    parser.add_argument("--glob", default="*.png", help="圖片檔名樣式")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    images = sorted(args.dir.glob(args.glob))
    if not images:
        print(f"[ERROR] {args.dir} 下找不到符合 {args.glob} 的圖片", file=sys.stderr)
        return 2

    preds = _load_predictions(args.dir / "eval_predictions.csv")
    preds = _predict_missing(images, preds)

    rows: list[tuple[str, str, str]] = []
    for path in images:
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        prefill = (preds.get(path.name, "") or "").upper()
        rows.append((path.name, b64, prefill))

    out = args.dir / "label_tool.html"
    out.write_text(_render_html(rows), encoding="utf-8")
    print(f"標註頁已產生：{out}")
    print(f"圖片數：{len(images)}（輸入框已預填 ddddocr 預測，轉大寫）")
    print("→ 用瀏覽器開啟，修正後按「下載 labels.csv」，再把 labels.csv 放回此資料夾。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
