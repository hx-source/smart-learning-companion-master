from __future__ import annotations

import argparse
from pathlib import Path

import fitz
from PIL import Image, ImageDraw


def render_pdf(pdf_path: Path, out_dir: Path, zoom: float = 1.4) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    pages: list[Path] = []
    matrix = fitz.Matrix(zoom, zoom)
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        out = out_dir / f"page-{i:02d}.png"
        pix.save(out)
        pages.append(out)
    doc.close()
    return pages


def contact_sheet(images: list[Path], out_path: Path, thumb_width: int = 360) -> None:
    loaded = [Image.open(p).convert("RGB") for p in images]
    thumbs = []
    for idx, img in enumerate(loaded, start=1):
        ratio = thumb_width / img.width
        thumb = img.resize((thumb_width, int(img.height * ratio)))
        canvas = Image.new("RGB", (thumb.width, thumb.height + 34), "white")
        canvas.paste(thumb, (0, 0))
        draw = ImageDraw.Draw(canvas)
        draw.text((8, thumb.height + 8), f"Page {idx}", fill=(0, 0, 0))
        thumbs.append(canvas)

    cols = 2
    gap = 24
    rows = (len(thumbs) + cols - 1) // cols
    cell_w = max(t.width for t in thumbs)
    cell_h = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * cell_w + (cols + 1) * gap, rows * cell_h + (rows + 1) * gap), "white")
    for i, thumb in enumerate(thumbs):
        r, c = divmod(i, cols)
        x = gap + c * (cell_w + gap)
        y = gap + r * (cell_h + gap)
        sheet.paste(thumb, (x, y))
    sheet.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="+")
    parser.add_argument("--out", default="completed_docs/qa_pages")
    args = parser.parse_args()
    base = Path(args.out)
    for raw in args.pdfs:
        pdf = Path(raw)
        doc_dir = base / pdf.stem
        pages = render_pdf(pdf, doc_dir)
        sheet = doc_dir / "contact-sheet.png"
        contact_sheet(pages, sheet)
        print(f"{pdf}: {len(pages)} pages -> {sheet}")


if __name__ == "__main__":
    main()
