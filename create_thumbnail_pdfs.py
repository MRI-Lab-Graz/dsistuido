import os
import glob
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader

def collect_images(root_dir, pattern):
    """Recursively collect image paths matching the pattern."""
    return sorted(glob.glob(os.path.join(root_dir, '**', pattern), recursive=True))

def parse_heading_from_path(img_path):
    """Extracts modality, effect size, and thresholds from the path or filename."""
    # Example folder: qa_0.2_25_4000
    # Example filename: .../qa_0.2_25_4000/somefile.inc.jpg
    parts = img_path.split(os.sep)
    # Only use the folder name, not the filename
    for i in range(len(parts) - 2, -1, -1):  # skip the filename part
        part = parts[i]
        if '_' in part and part.count('_') >= 3:
            tokens = part.split('_')
            if len(tokens) >= 4:
                modality = tokens[0]
                try:
                    effect_size = float(tokens[1])
                    threshold = int(tokens[2])
                    count = int(tokens[3])
                    return modality, effect_size, threshold, count
                except Exception:
                    continue
    # fallback: just filename
    return None, None, None, None

def draw_thumbnails(pdf_path, image_paths, title, grid=(5, 3), thumb_size=(120, 90)):
    """Create a PDF with thumbnails, sorted and grouped by effect size, with section headers."""
    # Parse and sort images by effect size and threshold
    parsed_images = []
    fallback_images = []
    for img_path in image_paths:
        modality, effect_size, threshold, count = parse_heading_from_path(img_path)
        if effect_size is not None and threshold is not None:
            parsed_images.append((effect_size, threshold, modality, img_path))
        else:
            fallback_images.append((img_path,))
    parsed_images.sort()  # sorts by effect_size, then threshold

    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4
    margin_x, margin_y = 20 * mm, 25 * mm
    spacing_x, spacing_y = 10, 30
    cols, rows = grid
    thumb_w, thumb_h = thumb_size
    images_per_page = cols * rows

    last_effect_size = None
    page_img_count = 0
    idx = 0
    for entry in parsed_images:
        effect_size, threshold, modality, img_path = entry
        # New page if needed
        if page_img_count == 0:
            if idx > 0:
                c.showPage()
            c.setFont("Helvetica-Bold", 18)
            c.drawString(margin_x, height - margin_y + 10, title)
            y_cursor = height - margin_y - 10
        # Section header for new effect size
        if effect_size != last_effect_size:
            y_cursor -= 20
            c.setFont("Helvetica-Bold", 13)
            c.drawString(margin_x, y_cursor, f"Effect size: {effect_size}")
            y_cursor -= 10
            last_effect_size = effect_size
            row = 0
            col = 0
            page_img_count = 0
        # Calculate position
        col = page_img_count % cols
        row = (page_img_count // cols) % rows
        x = margin_x + col * (thumb_w + spacing_x)
        y = y_cursor - row * (thumb_h + spacing_y)
        # Draw heading above each image
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x, y + 5, f"Threshold: {threshold} | Modality: {modality}")
        try:
            img = Image.open(img_path)
            img.thumbnail((thumb_w, thumb_h))
            img_reader = ImageReader(img)
            c.drawImage(img_reader, x, y - thumb_h, width=thumb_w, height=thumb_h)
        except Exception as e:
            c.setFont("Helvetica", 8)
            c.drawString(x, y - thumb_h / 2, f"Error: {e}")
        # Draw filename as caption
        c.setFont("Helvetica", 7)
        caption = os.path.relpath(img_path, start=os.path.dirname(pdf_path))
        c.drawString(x, y - thumb_h - 12, caption)
        page_img_count += 1
        if page_img_count == images_per_page:
            page_img_count = 0
        idx += 1
    # Add fallback images at the end
    if fallback_images:
        if page_img_count != 0:
            c.showPage()
        c.setFont("Helvetica-Bold", 13)
        c.drawString(margin_x, height - margin_y, "Unparsed Images")
        y_cursor = height - margin_y - 20
        page_img_count = 0
        for (img_path,) in fallback_images:
            col = page_img_count % cols
            row = (page_img_count // cols) % rows
            x = margin_x + col * (thumb_w + spacing_x)
            y = y_cursor - row * (thumb_h + spacing_y)
            c.setFont("Helvetica-Bold", 9)
            c.drawString(x, y + 5, "(unparsed)")
            try:
                img = Image.open(img_path)
                img.thumbnail((thumb_w, thumb_h))
                img_reader = ImageReader(img)
                c.drawImage(img_reader, x, y - thumb_h, width=thumb_w, height=thumb_h)
            except Exception as e:
                c.setFont("Helvetica", 8)
                c.drawString(x, y - thumb_h / 2, f"Error: {e}")
            c.setFont("Helvetica", 7)
            caption = os.path.relpath(img_path, start=os.path.dirname(pdf_path))
            c.drawString(x, y - thumb_h - 12, caption)
            page_img_count += 1
            if page_img_count == images_per_page:
                c.showPage()
                page_img_count = 0
    c.save()

def main():
    root_dir = "/Volumes/Thunder/dsi_crea/final_sweep"
    out_inc_pdf = "inc_thumbnails.pdf"
    out_dec_pdf = "dec_thumbnails.pdf"
    inc_images = collect_images(root_dir, "*.inc.jpg")
    dec_images = collect_images(root_dir, "*.dec.jpg")
    print(f"Found {len(inc_images)} .inc.jpg images, {len(dec_images)} .dec.jpg images.")
    draw_thumbnails(out_inc_pdf, inc_images, "Increase Results Overview")
    draw_thumbnails(out_dec_pdf, dec_images, "Decrease Results Overview")
    print(f"PDFs created: {out_inc_pdf}, {out_dec_pdf}")

if __name__ == "__main__":
    main()
