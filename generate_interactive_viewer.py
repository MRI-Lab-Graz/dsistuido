import json
import base64
import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image

ROOT_DEFAULT = "/Volumes/Thunder/dsi_crea/final_sweep"
OUTPUT_HTML = "interactive_viewer.html"


def parse_params(path: Path) -> Optional[Tuple[str, float, int, int]]:
    for part in reversed(path.parts[:-1]):
        tokens = part.split("_")
        if len(tokens) >= 4:
            try:
                return tokens[0], float(tokens[1]), int(tokens[2]), int(tokens[3])
            except ValueError:
                continue
    return None


def collect_images(root: Path, max_width: int = 800, jpeg_quality: int = 75) -> List[Dict]:
    patterns = ["*.inc.jpg", "*.dec.jpg"]
    records: List[Dict] = []
    for pattern in patterns:
        for img_path in root.rglob(pattern):
            parsed = parse_params(img_path)
            modality, effect, threshold, count = parsed if parsed else (None, None, None, None)
            suffixes = img_path.suffixes
            kind = "other"
            if len(suffixes) >= 2:
                if suffixes[-2] == ".inc":
                    kind = "inc"
                elif suffixes[-2] == ".dec":
                    kind = "dec"
            
            # Read, resize, compress and encode image as base64
            try:
                img = Image.open(img_path)
                # Resize if width exceeds max_width
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                
                # Compress to JPEG in memory
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=jpeg_quality, optimize=True)
                img_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
                data_uri = f"data:image/jpeg;base64,{img_data}"
            except Exception as e:
                print(f"Warning: Could not encode {img_path}: {e}")
                data_uri = ""
            
            records.append(
                {
                    "data_uri": data_uri,
                    "filename": img_path.name,
                    "kind": kind,
                    "modality": modality,
                    "effect": effect,
                    "threshold": threshold,
                    "count": count,
                }
            )
    return records


def build_html(data: List[Dict], root_dir: str) -> str:
    data_json = json.dumps(data, ensure_ascii=True)
    template = """<!DOCTYPE html>
<html>
<head>
<meta charset=\"utf-8\" />
<title>Connectometry Interactive Viewer</title>
<style>
body { font-family: Arial, sans-serif; margin: 16px; }
.controls { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
label { font-weight: bold; }
#images { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }
.card { border: 1px solid #ccc; padding: 8px; border-radius: 6px; background: #fafafa; }
.card img { width: 100%; height: auto; display: block; }
.badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-right: 4px; background: #e0e0e0; }
#status { margin-top: 8px; font-size: 14px; }
</style>
</head>
<body>
<h2>Connectometry Interactive Viewer</h2>
<div class="controls">
  <label for=\"kind\">Type</label>
  <select id=\"kind\">
    <option value=\"inc\">Increase</option>
    <option value=\"dec\">Decrease</option>
  </select>
  <label for=\"effect\">Effect size</label>
  <input id=\"effect\" type=\"range\" step=\"0.01\" />
  <span id=\"effectVal\"></span>
  <label for=\"threshold\">Threshold</label>
  <input id=\"threshold\" type=\"range\" step=\"1\" />
  <span id=\"thresholdVal\"></span>
</div>
<div id=\"status\"></div>
<div id=\"images\"></div>
<script>
const data = DATA_PLACEHOLDER;
const kindEl = document.getElementById('kind');
const effectEl = document.getElementById('effect');
const thresholdEl = document.getElementById('threshold');
const effectVal = document.getElementById('effectVal');
const thresholdVal = document.getElementById('thresholdVal');
const statusEl = document.getElementById('status');
const container = document.getElementById('images');

const effects = Array.from(new Set(data.map(d => d.effect).filter(v => v !== null))).sort((a, b) => a - b);
const thresholds = Array.from(new Set(data.map(d => d.threshold).filter(v => v !== null))).sort((a, b) => a - b);
if (!effects.length || !thresholds.length) {
  statusEl.textContent = 'No parsed effect/threshold values found.';
}
function nearest(arr, value) {
  if (!arr.length) return null;
  return arr.reduce((prev, curr) => Math.abs(curr - value) < Math.abs(prev - value) ? curr : prev, arr[0]);
}
function setSliderRanges() {
  if (effects.length) {
    effectEl.min = effects[0];
    effectEl.max = effects[effects.length - 1];
    effectEl.value = effects[0];
    effectVal.textContent = effects[0];
  }
  if (thresholds.length) {
    thresholdEl.min = thresholds[0];
    thresholdEl.max = thresholds[thresholds.length - 1];
    thresholdEl.value = thresholds[0];
    thresholdVal.textContent = thresholds[0];
  }
}
function render() {
  const targetEffect = nearest(effects, parseFloat(effectEl.value));
  const targetThreshold = nearest(thresholds, parseFloat(thresholdEl.value));
  effectVal.textContent = targetEffect !== null ? targetEffect : 'n/a';
  thresholdVal.textContent = targetThreshold !== null ? targetThreshold : 'n/a';
  const selectedKind = kindEl.value;
  const filtered = data.filter(d =>
    d.kind === selectedKind &&
    d.effect === targetEffect &&
    d.threshold === targetThreshold
  );
  statusEl.textContent = `${filtered.length} images for effect ${targetEffect}, threshold ${targetThreshold}, type ${selectedKind}`;
  container.innerHTML = '';
  filtered.forEach(d => {
    const card = document.createElement('div');
    card.className = 'card';
    const img = document.createElement('img');
    img.src = d.data_uri;
    img.alt = d.filename;
    const meta = document.createElement('div');
    meta.innerHTML = `
      <span class=\"badge\">Effect ${d.effect}</span>
      <span class=\"badge\">Thresh ${d.threshold}</span>
      <span class=\"badge\">Mod ${d.modality || 'n/a'}</span>
      <div style=\"font-size:12px; margin-top:4px;\">${d.filename}</div>
    `;
    card.appendChild(img);
    card.appendChild(meta);
    container.appendChild(card);
  });
}
[kindEl, effectEl, thresholdEl].forEach(el => el.addEventListener('input', render));
setSliderRanges();
render();
</script>
</body>
</html>
"""
    return template.replace("DATA_PLACEHOLDER", data_json)


def main(root_dir: str = ROOT_DEFAULT):
    root = Path(root_dir)
    if not root.exists():
        raise SystemExit(f"Root directory not found: {root}")
    data = collect_images(root)
    html = build_html(data, str(root))
    Path(OUTPUT_HTML).write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_HTML} with {len(data)} entries.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate an interactive viewer with sliders for effect size and threshold.")
    parser.add_argument("--root", default=ROOT_DEFAULT, help="Root directory containing the output images")
    args = parser.parse_args()
    main(args.root)
