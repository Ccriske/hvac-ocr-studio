#!/usr/bin/env python3
"""
HVAC OCR Studio — Lossless Vector-Preserving OCR & PDF Bookmarking Engine
=========================================================================
Overlays an invisible, font-size-matched OCR text layer directly onto the
original untouched PDF pages (preserving 100% crisp CAD vector linework),
automatically handles 90-degree rotated vertical title blocks, and embeds
a hierarchical PDF Bookmark (Outline) tree tailored for HVAC engineers:
  - Floor Plans
  - Schedules
  - Control Diagrams
  - Sequence of Operations
  - All Diagrams
  - Specifications (Div 23)
  - Equipment Cross-Reference
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import multiprocessing
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pypdf
import pypdfium2 as pdfium
import pytesseract
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

Image.MAX_IMAGE_PIXELS = None

EQUIPMENT_TAG_RE = re.compile(
    r"\b("
    r"AHU|RTU|VAV|FCU|EF|SF|RF|CH|CT|WP|HW|CW|HRV|ERV|DOAS|MAU|CU|ACU|"
    r"HP|UH|EUH|GUH|CRAC|CRAH|FSD|FD|SD|VFD|FTR|CUH|B|P"
    r")-\d{1,4}[A-Z]?\b"
)
AIRFLOW_CAPACITY_RE = re.compile(
    r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*"
    r"(?:CFM|GPM|MBH|BTUH|BTU/H|TONS?|HP|ESP|WC|IN\.?\s*W\.?G\.?|FLA|MCA|MOP)\b",
    re.IGNORECASE,
)
CSI_DIV23_RE = re.compile(r"\b(?:SECTION\s+)?(23\s?\d{2}\s?\d{2}(?:\.\d{2})?)\b", re.IGNORECASE)
SHEET_NUMBER_RE = re.compile(r"\b([MH]-?\d{1,3}(?:\.\d{1,2})?[A-Z]?)\b")


@dataclass
class PageResult:
    page_number: int
    ocr_applied: bool
    categories: List[str] = field(default_factory=list)
    is_any_diagram: bool = False
    inferred_title: str = ""
    sheet_numbers: List[str] = field(default_factory=list)
    equipment_tags: List[str] = field(default_factory=list)
    capacities: List[str] = field(default_factory=list)
    csi_sections: List[str] = field(default_factory=list)
    title_block_lines: List[str] = field(default_factory=list)
    schedule_lines: List[str] = field(default_factory=list)
    text: str = ""


def sanitize_ascii(text: str) -> str:
    return re.sub(r"[^\x20-\x7E]", " ", text or "").strip()


def extract_entities(text: str) -> Tuple[List[str], List[str], List[str], List[str]]:
    tags = sorted({m.group(0).upper() for m in EQUIPMENT_TAG_RE.finditer(text)})
    caps = sorted({" ".join(m.group(0).upper().split()) for m in AIRFLOW_CAPACITY_RE.finditer(text)})
    csi = sorted({" ".join(m.group(1).split()) for m in CSI_DIV23_RE.finditer(text)})
    sheets = sorted({m.group(1).upper() for m in SHEET_NUMBER_RE.finditer(text)})
    return sheets, tags, caps, csi


def classify_page(
    text: str,
    sheets: List[str],
    width_pt: float,
    height_pt: float,
    title_block_lines: List[str],
    largest_lines: List[str],
) -> Tuple[List[str], bool, str, List[str]]:
    upper = text.upper()
    is_landscape = width_pt > height_pt
    cats = []

    is_floorplan = bool(
        re.search(r"\b(FLOOR\s*PLAN|HVAC\s*PLAN|MECHANICAL\s*PLAN|DUCTWORK\s*PLAN|PIPING\s*PLAN|ROOF\s*PLAN)\b", upper)
        or any(re.match(r"^M-?1\d{2}", s) for s in sheets)
    )
    is_control = bool(
        re.search(r"\b(CONTROL\s*DIAGRAM|CONTROLS\s*DIAGRAM|DDC\s*DIAGRAM|BAS\s*DIAGRAM|POINTS\s*LIST|WIRING\s*DIAGRAM)\b", upper)
        or any(re.match(r"^M-?[78]\d{2}", s) for s in sheets)
    )
    is_soo = bool(
        re.search(r"\b(SEQUENCE\s+OF\s+OPERATIONS?|SEQUENCES\s+OF\s+OPERATION|CONTROL\s+SEQUENCE|23\s?09\s?93)\b", upper)
    )
    is_diagram = bool(
        is_control
        or re.search(r"\b(FLOW\s*DIAGRAM|SCHEMATIC|RISER\s*DIAGRAM|AIRFLOW\s*DIAGRAM|HYDRONIC\s*DIAGRAM|DIAGRAM)\b", upper)
        or any(re.match(r"^M-?5\d{2}", s) for s in sheets)
    )
    is_schedule = bool(
        re.search(r"\b(EQUIPMENT\s*SCHEDULE|AHU\s*SCHEDULE|RTU\s*SCHEDULE|VAV\s*SCHEDULE|FAN\s*SCHEDULE|MECHANICAL\s*SCHEDULES?)\b", upper)
        or any(re.match(r"^M-?6\d{2}", s) for s in sheets)
    )
    is_spec = bool(re.search(r"\b(PART\s+1\s*[-–]\s*GENERAL|SECTION\s+23\s?\d{2}\s?\d{2})\b", upper))

    if is_floorplan:
        cats.append("floorplan")
    if is_control:
        cats.append("control_diagram")
    if is_soo:
        cats.append("sequence_of_ops")
    if is_diagram and not is_control:
        cats.append("diagram")
    if is_schedule:
        cats.append("schedule")
    if is_spec and not is_soo:
        cats.append("spec")
    if not cats:
        cats.append("floorplan" if is_landscape else "spec")

    sched_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and re.search(r"\b(SCHEDULE|CFM|GPM|TONS?|MBH|ESP|HP|AHU-\d|RTU-\d|VAV-\d|EF-\d)\b", ln, re.I)
    ]

    inferred_title = (
        largest_lines[0]
        if largest_lines
        else (title_block_lines[0] if title_block_lines else (text.splitlines()[0][:72] if text.splitlines() else "Untitled Sheet"))
    )
    return cats, is_diagram, inferred_title[:80], sched_lines


def build_invisible_text_overlay(
    width_pt: float,
    height_pt: float,
    words: List[dict],
) -> bytes:
    """
    Create a single-page transparent PDF containing ONLY invisible (Tr 3)
    searchable text matched to each word's exact position, font height, width,
    and rotation angle so it overlays losslessly onto the original vector PDF.
    """
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(width_pt, height_pt))
    for w in words:
        txt = sanitize_ascii(w["text"])
        if not txt:
            continue
        font_size = max(4.0, min(w["h_pt"] * 0.92, 72.0))
        measured_w = stringWidth(txt, "Helvetica", font_size)
        h_scale = 100.0
        if measured_w > 0.5 and w["w_pt"] > 1.0:
            h_scale = max(40.0, min(250.0, (w["w_pt"] / measured_w) * 100.0))

        c.saveState()
        c.translate(w["x_pt"], w["y_pt"])
        if w.get("rot", 0):
            c.rotate(w["rot"])
        tobj = c.beginText(0, 0)
        tobj.setFont("Helvetica", font_size)
        tobj.setHorizScale(h_scale)
        tobj.setTextRenderMode(3)  # Invisible, selectable & searchable
        tobj.textOut(txt)
        c.drawText(tobj)
        c.restoreState()

    c.showPage()
    c.save()
    return buf.getvalue()


def ocr_page_lossless(
    pdf_path: str,
    page_index: int,
    width_pt: float,
    height_pt: float,
    tesseract_cmd: Optional[str] = None,
) -> Tuple[int, Optional[bytes], str, List[str], List[str]]:
    """
    Render one page to memory, run Tesseract OCR (plus 90 deg CW right-edge
    title block OCR on landscape sheets), and return a transparent invisible-text
    overlay PDF byte stream so the original vector PDF page stays 100% untouched.
    """
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    is_landscape = width_pt > height_pt
    longest_in = max(width_pt, height_pt) / 72.0
    target_dpi = min(200.0, 4200.0 / max(longest_in, 1.0))
    scale = target_dpi / 72.0

    doc = pdfium.PdfDocument(pdf_path)
    try:
        page = doc[page_index]
        pil_img = page.render(scale=scale).to_pil()
        page.close()
    finally:
        doc.close()

    gray = ImageOps.autocontrast(ImageOps.grayscale(pil_img), cutoff=1)
    if is_landscape:
        gray = ImageEnhance.Contrast(gray).enhance(1.35).filter(ImageFilter.SHARPEN)

    img_w, img_h = gray.size
    sx = width_pt / max(1, img_w)
    sy = height_pt / max(1, img_h)

    psm = 11 if is_landscape else 3
    data = pytesseract.image_to_data(
        gray, lang="eng", config=f"--oem 3 --psm {psm}", output_type=pytesseract.Output.DICT
    )

    overlay_words: List[dict] = []
    grouped_lines: dict[Tuple[int, int, int], list] = {}
    tb_lines: List[str] = []

    for i, raw in enumerate(data.get("text", [])):
        txt = (raw or "").strip()
        if not txt:
            continue
        left, top, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped_lines.setdefault(key, []).append((txt, left, top, h))
        overlay_words.append({
            "text": txt,
            "x_pt": left * sx,
            "y_pt": height_pt - (top + h) * sy,
            "w_pt": w * sx,
            "h_pt": h * sy,
            "rot": 0,
        })

    line_texts_with_size: List[Tuple[float, str]] = []
    for items in grouped_lines.values():
        line_str = " ".join(it[0] for it in items)
        avg_h_pt = (sum(it[3] for it in items) / max(1, len(items))) * sy
        min_x_norm = min(it[1] for it in items) / max(1, img_w)
        line_texts_with_size.append((avg_h_pt, line_str))
        if is_landscape and min_x_norm >= 0.80:
            tb_lines.append(line_str)

    full_text = "\n".join(ln for _, ln in line_texts_with_size)
    largest_lines = [ln for sz, ln in sorted(line_texts_with_size, key=lambda x: -x[0]) if sz >= 14.0][:5]

    # 90° clockwise rotation pass on right-edge vertical title block for landscape sheets
    if is_landscape:
        crop_x = int(img_w * 0.80)
        tb_crop = gray.crop((crop_x, 0, img_w, img_h))
        tb_rot = tb_crop.rotate(-90, expand=True)  # -90 in Pillow = 90 deg CW
        tb_data = pytesseract.image_to_data(
            tb_rot, lang="eng", config="--oem 3 --psm 6", output_type=pytesseract.Output.DICT
        )
        tb_grouped: dict[Tuple[int, int, int], list] = {}
        for i, raw in enumerate(tb_data.get("text", [])):
            txt = (raw or "").strip()
            if not txt:
                continue
            left, top, w, h = tb_data["left"][i], tb_data["top"][i], tb_data["width"][i], tb_data["height"][i]
            key = (tb_data["block_num"][i], tb_data["par_num"][i], tb_data["line_num"][i])
            tb_grouped.setdefault(key, []).append(txt)
            # Map rotated crop coords back to original sheet coords (rotated +90 deg)
            orig_x = crop_x + top + h
            orig_y_bottom = img_h - left
            overlay_words.append({
                "text": txt,
                "x_pt": orig_x * sx,
                "y_pt": height_pt - orig_y_bottom * sy,
                "w_pt": w * sy,
                "h_pt": h * sx,
                "rot": 90,
            })

        rot_lines = [
            " ".join(ws)
            for ws in tb_grouped.values()
            if len(re.sub(r"[^A-Za-z0-9]", "", " ".join(ws))) >= 3
        ]
        if rot_lines:
            tb_lines = list(dict.fromkeys(rot_lines + tb_lines))
            full_text = (full_text + "\n\n[TITLE BLOCK]\n" + "\n".join(rot_lines)).strip()

    overlay_pdf = build_invisible_text_overlay(width_pt, height_pt, overlay_words) if overlay_words else None
    return page_index, overlay_pdf, full_text, tb_lines, largest_lines


def add_engineering_bookmarks(writer: pypdf.PdfWriter, results: List[PageResult]) -> None:
    """Embed a clean, hierarchical PDF Outline (Bookmarks) tree for Bluebeam / Acrobat."""
    sections = [
        ("Floor Plans", [r for r in results if "floorplan" in r.categories]),
        ("Schedules", [r for r in results if "schedule" in r.categories]),
        ("Control Diagrams", [r for r in results if "control_diagram" in r.categories]),
        ("Sequence of Operations", [r for r in results if "sequence_of_ops" in r.categories]),
        ("All Diagrams & Schematics", [r for r in results if r.is_any_diagram]),
        ("Specifications (Div 23)", [r for r in results if "spec" in r.categories]),
    ]

    for heading, items in sections:
        if not items:
            continue
        parent = writer.add_outline_item(f"{heading} ({len(items)})", items[0].page_number - 1)
        for item in items:
            tag = f"[{item.sheet_numbers[0]}] " if item.sheet_numbers else ""
            label = f"{tag}{item.inferred_title} (p.{item.page_number})"
            writer.add_outline_item(label, item.page_number - 1, parent=parent)

    # Equipment Cross-Reference tree
    tag_map: dict[str, List[PageResult]] = {}
    for r in results:
        for t in r.equipment_tags:
            tag_map.setdefault(t, []).append(r)

    if tag_map:
        first_page = next(iter(tag_map.values()))[0].page_number - 1
        eq_root = writer.add_outline_item(f"Equipment Cross-Reference ({len(tag_map)})", first_page)
        cat_names = {
            "floorplan": "Floor Plan",
            "schedule": "Schedule",
            "control_diagram": "Control Diagram",
            "sequence_of_ops": "Sequence of Ops",
            "diagram": "Diagram",
            "spec": "Spec",
        }
        for tag in sorted(tag_map.keys()):
            pages = tag_map[tag]
            tag_node = writer.add_outline_item(tag, pages[0].page_number - 1, parent=eq_root)
            for r in pages:
                cat = cat_names.get(r.categories[0], "Sheet") if r.categories else "Sheet"
                sh = f" ({r.sheet_numbers[0]})" if r.sheet_numbers else ""
                writer.add_outline_item(f"{cat}{sh} - p.{r.page_number}", r.page_number - 1, parent=tag_node)


def process_pdf_lossless(
    input_pdf: Path,
    output_pdf: Optional[Path] = None,
    workers: Optional[int] = None,
    progress_cb: Optional[Callable[[int, str], None]] = None,
) -> dict:
    """
    Process a PDF losslessly: preserve crisp vector drawings, overlay invisible
    font-matched OCR text on scanned/SHX pages + rotated title blocks, and embed
    native hierarchical PDF bookmarks.
    """
    if output_pdf is None:
        output_pdf = input_pdf.with_name(f"{input_pdf.stem}_searchable.pdf")

    reader = pypdf.PdfReader(str(input_pdf))
    total = len(reader.pages)
    if progress_cb:
        progress_cb(5, f"Inspecting {total} page(s)...")

    existing_texts: List[str] = []
    page_dims: List[Tuple[float, float]] = []
    ocr_indices: List[int] = []

    for idx, page in enumerate(reader.pages):
        txt = (page.extract_text() or "").strip()
        existing_texts.append(txt)
        w, h = float(page.mediabox.width), float(page.mediabox.height)
        page_dims.append((w, h))
        # OCR pages with minimal selectable text (scanned or stroked AutoCAD SHX)
        if len(txt) < 80:
            ocr_indices.append(idx)

    tess_cmd = pytesseract.pytesseract.tesseract_cmd
    ocr_map: dict[int, Tuple[Optional[bytes], str, List[str], List[str]]] = {}

    if ocr_indices:
        max_w = workers or max(1, min((os.cpu_count() or 2), 6))
        actual_w = max(1, min(max_w, len(ocr_indices)))
        if progress_cb:
            progress_cb(15, f"Running parallel OCR on {len(ocr_indices)} page(s) ({actual_w} cores)...")

        done = 0
        if actual_w == 1:
            for idx in ocr_indices:
                w, h = page_dims[idx]
                _, ov, txt, tb, lg = ocr_page_lossless(str(input_pdf), idx, w, h, tess_cmd)
                ocr_map[idx] = (ov, txt, tb, lg)
                done += 1
                if progress_cb:
                    progress_cb(15 + int((done / len(ocr_indices)) * 70), f"OCR finished {done}/{len(ocr_indices)} page(s)...")
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=actual_w) as pool:
                futs = {
                    pool.submit(ocr_page_lossless, str(input_pdf), idx, page_dims[idx][0], page_dims[idx][1], tess_cmd): idx
                    for idx in ocr_indices
                }
                for fut in concurrent.futures.as_completed(futs):
                    idx, ov, txt, tb, lg = fut.result()
                    ocr_map[idx] = (ov, txt, tb, lg)
                    done += 1
                    if progress_cb:
                        progress_cb(15 + int((done / len(ocr_indices)) * 70), f"OCR finished {done}/{len(ocr_indices)} page(s)...")

    if progress_cb:
        progress_cb(88, "Merging lossless text layer & building PDF bookmarks...")

    writer = pypdf.PdfWriter()
    results: List[PageResult] = []

    for idx in range(total):
        w, h = page_dims[idx]
        orig_page = reader.pages[idx]
        if idx in ocr_map:
            ov_bytes, txt, tb_lines, lg_lines = ocr_map[idx]
            if ov_bytes:
                ov_page = pypdf.PdfReader(io.BytesIO(ov_bytes)).pages[0]
                orig_page.merge_page(ov_page)
            ocr_applied = True
        else:
            txt = existing_texts[idx]
            tb_lines = []
            lg_lines = [ln.strip() for ln in txt.splitlines()[:3] if ln.strip()]
            ocr_applied = False

        writer.add_page(orig_page)
        sheets, tags, caps, csi = extract_entities(txt)
        cats, is_diag, title, sched_lines = classify_page(txt, sheets, w, h, tb_lines, lg_lines)
        results.append(
            PageResult(
                page_number=idx + 1,
                ocr_applied=ocr_applied,
                categories=cats,
                is_any_diagram=is_diag,
                inferred_title=title,
                sheet_numbers=sheets,
                equipment_tags=tags,
                capacities=caps,
                csi_sections=csi,
                title_block_lines=tb_lines,
                schedule_lines=sched_lines,
                text=txt,
            )
        )

    add_engineering_bookmarks(writer, results)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with open(output_pdf, "wb") as f:
        writer.write(f)

    if progress_cb:
        progress_cb(100, f"Saved {output_pdf.name}")

    return {
        "source_file": str(input_pdf),
        "output_pdf": str(output_pdf),
        "total_pages": total,
        "pages_ocr_processed": len(ocr_indices),
        "pages": [asdict(r) for r in results],
    }


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) < 2:
        print("Usage: python hvac_pdf_ocr.py <input.pdf> [output.pdf]")
        raise SystemExit(1)
    inp = Path(sys.argv[1])
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    rep = process_pdf_lossless(inp, out, progress_cb=lambda p, m: print(f"[{p:3d}%] {m}"))
    print(f"Done: {rep['output_pdf']} ({rep['pages_ocr_processed']}/{rep['total_pages']} OCR'd)")
