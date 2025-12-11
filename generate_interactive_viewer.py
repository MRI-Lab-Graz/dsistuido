import json
import base64
import io
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image

ROOT_DEFAULT = "/Volumes/Thunder/dsi_crea/final_sweep"
OUTPUT_HTML_NAME = "interactive_viewer.html"


def encode_image_to_data_uri(img: Image.Image, max_width: Optional[int], jpeg_quality: int) -> str:
  """Resize (if requested) and JPEG-encode an image to a data URI."""
  if max_width and max_width > 0 and img.width > max_width:
    ratio = max_width / img.width
    new_height = int(img.height * ratio)
    img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)

  buffer = io.BytesIO()
  img.save(buffer, format='JPEG', quality=jpeg_quality, optimize=True, subsampling=0)
  img_data = base64.b64encode(buffer.getvalue()).decode('utf-8')
  return f"data:image/jpeg;base64,{img_data}"


def parse_params(path: Path) -> Optional[Tuple[str, float, int, int]]:
  # Walk up directory parts (excluding the filename) and look for a directory
  # that matches the pattern: <modality>_<effect>_<threshold>_<count>
  # Modality itself may contain underscores (e.g. 'dti_fa'), so we treat
  # the last three underscore-separated tokens as numbers and everything
  # before that as modality name.
  for part in reversed(path.parts[:-1]):
    tokens = part.split("_")
    if len(tokens) >= 4:
      # modality may contain underscores: join all tokens except last 3
      modality_token = "_".join(tokens[:-3])
      effect_token = tokens[-3]
      thresh_token = tokens[-2]
      count_token = tokens[-1]
      try:
        effect = float(effect_token)
        thresh = int(float(thresh_token))
        count = int(float(count_token))
        return modality_token, effect, thresh, count
      except ValueError:
        # not a matching pattern, continue searching up
        continue
    return None


def get_image_data_uri(img_path: Path, max_width: Optional[int], jpeg_quality: int) -> str:
  """Read image and return data URI. Uses direct read if no resize needed."""
  if not max_width or max_width <= 0:
    try:
      with open(img_path, "rb") as f:
        img_data = base64.b64encode(f.read()).decode('utf-8')
        return f"data:image/jpeg;base64,{img_data}"
    except Exception as e:
      print(f"Warning: Could not read {img_path}: {e}")
      return ""
  else:
    try:
      img = Image.open(img_path)
      return encode_image_to_data_uri(img, max_width, jpeg_quality)
    except Exception as e:
      print(f"Warning: Could not encode {img_path}: {e}")
      return ""


def collect_images(
  root: Path,
  max_width: Optional[int] = 800,
  jpeg_quality: int = 75,
  verbose: bool = True,
  require_tt: bool = True,
  tt_min_bytes: int = 2048,
  use_placeholder: bool = True,
  custom_placeholder_path: Optional[Path] = None,
) -> Tuple[List[Dict], Optional[str]]:
  patterns = ["*.inc.jpg", "*.dec.jpg"]
  records: List[Dict] = []
  total_found = 0
  placeholder_data_uri = None

  # Pre-load custom placeholder if provided
  if custom_placeholder_path and custom_placeholder_path.exists():
    if verbose:
      print(f"Loading custom placeholder from {custom_placeholder_path}")
    placeholder_data_uri = get_image_data_uri(custom_placeholder_path, max_width, jpeg_quality)

  if verbose:
    print(f"Scanning for images under: {root}")
  for pattern in patterns:
    for img_path in root.rglob(pattern):
      total_found += 1
      if verbose and total_found % 50 == 0:
        print(f"  Found {total_found} images so far...")

      parsed = parse_params(img_path)
      modality, effect, threshold, count = parsed if parsed else (None, None, None, None)
      suffixes = img_path.suffixes
      kind = "other"
      if len(suffixes) >= 2:
        if suffixes[-2] == ".inc":
          kind = "inc"
        elif suffixes[-2] == ".dec":
          kind = "dec"

      # Check for tt.gz
      if require_tt and kind in ("inc", "dec"):
        fname = img_path.name
        suffix_to_strip = f".{kind}.jpg"
        if fname.endswith(suffix_to_strip):
          prefix = fname[:-len(suffix_to_strip)]
          tt_path = img_path.with_name(prefix + f".{kind}.tt.gz")
        else:
          tt_path = img_path.with_suffix(f".{kind}.tt.gz")

        if not tt_path.exists() or tt_path.stat().st_size < tt_min_bytes:
          if not use_placeholder:
            if verbose:
              print(f"  Skipping {img_path} (missing/small tt.gz)")
            continue
          
          # We are using a placeholder
          if placeholder_data_uri is None:
            # First missing entry becomes the placeholder source
            if verbose:
              print(f"  Using {img_path} as the shared placeholder source.")
            placeholder_data_uri = get_image_data_uri(img_path, max_width, jpeg_quality)
          
          # Add record with None data_uri (will use shared placeholder in JS)
          records.append({
            "data_uri": None,
            "filename": img_path.name,
            "kind": kind,
            "modality": modality,
            "effect": effect,
            "threshold": threshold,
            "count": count,
          })
          continue

      # Normal image processing
      data_uri = get_image_data_uri(img_path, max_width, jpeg_quality)
      records.append({
        "data_uri": data_uri,
        "filename": img_path.name,
        "kind": kind,
        "modality": modality,
        "effect": effect,
        "threshold": threshold,
        "count": count,
      })

  if verbose:
    print(f"Done scanning. Total images found: {total_found}")
  return records, placeholder_data_uri


def build_html(data: List[Dict], root_dir: str, placeholder_data_uri: Optional[str]) -> str:
  data_json = json.dumps(data, ensure_ascii=True)
  placeholder_json = json.dumps(placeholder_data_uri, ensure_ascii=True)
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
  <label for=\"modality\">Modality</label>
  <select id=\"modality\"></select>
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
const placeholderData = PLACEHOLDER_PLACEHOLDER;
const modalityEl = document.getElementById('modality');
const kindEl = document.getElementById('kind');
const effectEl = document.getElementById('effect');
const thresholdEl = document.getElementById('threshold');
const effectVal = document.getElementById('effectVal');
const thresholdVal = document.getElementById('thresholdVal');
const statusEl = document.getElementById('status');
const container = document.getElementById('images');

const modalities = Array.from(new Set(data.map(d => d.modality).filter(v => v !== null))).sort();
modalities.forEach(m => {
  const opt = document.createElement('option');
  opt.value = m;
  opt.textContent = m;
  modalityEl.appendChild(opt);
});

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
  const selectedModality = modalityEl.value;
  const filtered = data.filter(d =>
    d.modality === selectedModality &&
    d.kind === selectedKind &&
    d.effect === targetEffect &&
    d.threshold === targetThreshold
  );
  statusEl.textContent = `${filtered.length} images for modality ${selectedModality}, effect ${targetEffect}, threshold ${targetThreshold}, type ${selectedKind}`;
  container.innerHTML = '';
  filtered.forEach(d => {
    const card = document.createElement('div');
    card.className = 'card';
    const img = document.createElement('img');
    img.src = d.data_uri || placeholderData;
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
[modalityEl, kindEl, effectEl, thresholdEl].forEach(el => el.addEventListener('input', render));
setSliderRanges();
render();
</script>
</body>
</html>
"""
  return template.replace("DATA_PLACEHOLDER", data_json).replace("PLACEHOLDER_PLACEHOLDER", placeholder_json)


def main(
  root_dir: str = ROOT_DEFAULT,
  output_dir: Optional[str] = None,
  output_name: Optional[str] = None,
  jpeg_quality: int = 75,
  require_tt: bool = True,
  tt_min_bytes: int = 2048,
  max_width: Optional[int] = 800,
  placeholder: Optional[str] = None,
  placeholder_quality: Optional[int] = None,
  enable_placeholder: bool = True,
):
  root = Path(root_dir)
  if not root.exists():
    raise SystemExit(f"Root directory not found: {root}")

  custom_placeholder_path = Path(placeholder) if placeholder else None

  # Verbose collection feedback
  data, placeholder_data_uri = collect_images(
      root,
      max_width=max_width,
      jpeg_quality=jpeg_quality,
      verbose=True,
      require_tt=require_tt,
      tt_min_bytes=tt_min_bytes,
      use_placeholder=enable_placeholder,
      custom_placeholder_path=custom_placeholder_path,
  )
  html = build_html(data, str(root), placeholder_data_uri)

  # Determine output folder
  if output_dir:
    out_folder = Path(output_dir)
    out_folder.mkdir(parents=True, exist_ok=True)
  else:
    out_folder = root

  # Determine output file name
  fname = output_name if output_name else OUTPUT_HTML_NAME
  output_path = out_folder / fname
  output_path.write_text(html, encoding="utf-8")
  print(f"Wrote {output_path} with {len(data)} entries.")


if __name__ == "__main__":
  import argparse
  import sys

  parser = argparse.ArgumentParser(
      description="Generate an interactive viewer with sliders for effect size and threshold."
  )
  parser.add_argument("input_folder", nargs="?", default=ROOT_DEFAULT, help="Root directory containing the output images")
  parser.add_argument("--output", "-o", dest="output", default=None, help="Output folder to write the HTML (default: input folder)")
  parser.add_argument("--output-name", dest="output_name", default=None, help=f"Output HTML filename (default: {OUTPUT_HTML_NAME})")
  parser.add_argument("--jpeg-quality", dest="jpeg_quality", type=int, default=75, help="JPEG quality for embedded images (1-100). Lower reduces file size; default 75.")
  parser.add_argument("--require-tt", dest="require_tt", action="store_true", default=True, help="Check for tt.gz; if missing/too-small, show placeholder instead (default: on)")
  parser.add_argument("--no-require-tt", dest="require_tt", action="store_false", help="Do not check for tt.gz (still shows the image)")
  parser.add_argument("--tt-min-bytes", dest="tt_min_bytes", type=int, default=2048, help="Minimum tt.gz size in bytes to keep an image (default 2048)")
  parser.add_argument("--max-width", dest="max_width", type=int, default=800, help="Resize images to this max width before embedding (default 800; set 0 to disable resizing)")
  parser.add_argument("--placeholder", dest="placeholder", default=None, help="Path to an image used for entries missing/too-small tt.gz (default: generated blank)")
  parser.add_argument("--placeholder-quality", dest="placeholder_quality", type=int, default=None, help="JPEG quality for placeholder encoding (default: same as --jpeg-quality)")
  parser.add_argument("--no-placeholder", dest="enable_placeholder", action="store_false", help="Do not show a placeholder for missing/too-small tt.gz; drop non-significant entries entirely")
  # If no arguments were passed, show help and exit
  if len(sys.argv) == 1:
    parser.print_help()
    sys.exit(0)

  args = parser.parse_args()
  main(
      args.input_folder,
      args.output,
      args.output_name,
      args.jpeg_quality,
      args.require_tt,
      args.tt_min_bytes,
        args.max_width,
        args.placeholder,
        args.placeholder_quality,
        args.enable_placeholder,
  )
