#!/usr/bin/env python3
"""
HVAC Plan & Spec OCR + Sheet Classification & Linking Tool
==========================================================
Converts non-searchable HVAC plans and specs into text-searchable PDFs,
classifies text lines by estimated font size, isolates Title Blocks and
Equipment Schedules, and cross-references Floor Plans, Control Diagrams,
Sequence of Operations, and All Diagrams to TXT, JSON, and CSV.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import io
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
import pypdf
import pypdfium2 as pdfium
import pytesseract

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
class PageExtraction:
    page_number: int
    ocr_applied: bool
    char_count: int
    categories: List[str] = field(default_factory=list)
    is_any_diagram: bool = False
    inferred_title: str = ""
    sheet_numbers: List[str] = field(default_factory=list)
    equipment_tags: List[str] = field(default_factory=list)
    capacities: List[str] = field(default_factory=list)
    csi_sections: List[str] = field(default_factory=list)
    title_block_lines: List[str] = field(default_factory=list)
    schedule_lines: List[str] = field(default_factory=list)
    font_tier_counts: dict = field(default_factory=dict)


@dataclass
class DocumentReport:
    source_file: str
    output_pdf: str
    total_pages: int
    pages_ocr_processed: int
    pages_preserved: int
    links: dict
    unique_equipment_tags: List[str]
    unique_csi_sections: List[str]
    unique_sheet_numbers: List[str]
    pages: List[PageExtraction]


def preprocess_for_ocr(image: Image.Image, mode: str) -> Image.Image:
    gray = ImageOps.grayscale(image)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    if mode == "plan":
        gray = ImageEnhance.Contrast(gray).enhance(1.4)
        gray = gray.filter(ImageFilter.SHARPEN)
    return gray


def choose_effective_dpi(width_pt: float, height_pt: float, target_dpi: int, max_pixel_dim: int) -> float:
    longest_in = max(max(width_pt / 72.0, 0.1), max(height_pt / 72.0, 0.1))
    return min(float(target_dpi), max_pixel_dim / longest_in)


def resolve_page_mode(width_pt: float, height_pt: float, mode: str) -> str:
    if mode in ("plan", "spec"):
        return mode
    return "plan" if max(width_pt, height_pt) / 72.0 > 14.5 else "spec"


def classify_font_tier(font_pt: float) -> str:
    if font_pt >= 18.0:
        return "title"
    if font_pt >= 12.0:
        return "heading"
    if font_pt >= 7.0:
        return "callout"
    return "fine"


def extract_hvac_entities(text: str) -> Tuple[List[str], List[str], List[str], List[str]]:
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
    line_records: List[dict],
) -> Tuple[List[str], bool, str, List[str], List[str], dict]:
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

    tb_lines = [r["text"] for r in line_records if r.get("in_title_block")]
    sched_lines = [
        r["text"]
        for r in line_records
        if not r.get("in_title_block")
        and re.search(r"\b(SCHEDULE|CFM|GPM|TONS?|MBH|ESP|HP|AHU-\d|RTU-\d|VAV-\d|EF-\d)\b", r["text"], re.I)
    ]
    tier_counts = {"title": 0, "heading": 0, "callout": 0, "fine": 0}
    titles = []
    for r in line_records:
        t = r.get("tier", "callout")
        tier_counts[t] = tier_counts.get(t, 0) + 1
        if t in ("title", "heading"):
            titles.append(r["text"])

    inferred_title = titles[0] if titles else (text.splitlines()[0][:70] if text.splitlines() else "Untitled Sheet")
    return cats, is_diagram, inferred_title, tb_lines, sched_lines, tier_counts


def ocr_single_page(
    pdf_path: str,
    page_index: int,
    width_pt: float,
    height_pt: float,
    target_dpi: int,
    max_pixel_dim: int,
    mode: str,
    lang: str,
    oem: int,
) -> Tuple[int, bytes, str, List[dict]]:
    page_mode = resolve_page_mode(width_pt, height_pt, mode)
    effective_dpi = choose_effective_dpi(width_pt, height_pt, target_dpi, max_pixel_dim)
    scale = effective_dpi / 72.0

    doc = pdfium.PdfDocument(pdf_path)
    try:
        page = doc[page_index]
        pil_img = page.render(scale=scale).to_pil()
        page.close()
    finally:
        doc.close()

    processed = preprocess_for_ocr(pil_img, page_mode)
    processed.info["dpi"] = (round(effective_dpi), round(effective_dpi))
    psm = 11 if page_mode == "plan" else 3
    tess_config = f"--oem {oem} --psm {psm}"

    pdf_bytes = pytesseract.image_to_pdf_or_hocr(processed, lang=lang, extension="pdf", config=tess_config)
    ocr_reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    extracted_text = (ocr_reader.pages[0].extract_text() or "").strip()

    # Extract line bounding boxes for font-size tiering & title-block isolation
    data = pytesseract.image_to_data(processed, lang=lang, config=tess_config, output_type=pytesseract.Output.DICT)
    img_w, img_h = processed.size
    scale_y = height_pt / max(1, img_h)
    is_landscape = width_pt > height_pt

    grouped: dict[Tuple[int, int, int], list] = {}
    for i, word in enumerate(data.get("text", [])):
        wtxt = (word or "").strip()
        if not wtxt:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append((wtxt, data["left"][i], data["top"][i], data["height"][i]))

    line_records = []
    for items in grouped.values():
        txt = " ".join(it[0] for it in items)
        min_x = min(it[1] for it in items)
        min_y = min(it[2] for it in items)
        avg_h = sum(it[3] for it in items) / max(1, len(items))
        font_pt = max(4.0, avg_h * scale_y)
        x_norm = min_x / max(1, img_w)
        y_norm = min_y / max(1, img_h)
        in_tb = (is_landscape and x_norm >= 0.78) or (is_landscape and x_norm >= 0.65 and y_norm >= 0.78)
        line_records.append({
            "text": txt,
            "font_pt": round(font_pt, 1),
            "tier": classify_font_tier(font_pt),
            "in_title_block": in_tb,
        })

    return page_index, pdf_bytes, extracted_text, line_records


def normalize_page_size(ocr_page: pypdf.PageObject, target_w: float, target_h: float) -> pypdf.PageObject:
    cur_w, cur_h = float(ocr_page.mediabox.width), float(ocr_page.mediabox.height)
    if cur_w > 0 and cur_h > 0:
        sx, sy = target_w / cur_w, target_h / cur_h
        if abs(sx - 1.0) > 0.005 or abs(sy - 1.0) > 0.005:
            ocr_page.scale(sx=sx, sy=sy)
    return ocr_page


def process_pdf(
    input_pdf: Path,
    output_dir: Path,
    dpi: int = 300,
    max_pixel_dim: int = 6000,
    mode: str = "auto",
    lang: str = "eng",
    oem: int = 3,
    min_chars: int = 50,
    force_ocr: bool = False,
    workers: int = 1,
    export_text: bool = False,
    export_json: bool = False,
    export_csv: bool = False,
    verbose: bool = True,
) -> DocumentReport:
    output_dir.mkdir(parents=True, exist_ok=True)
    out_pdf_path = output_dir / f"{input_pdf.stem}_searchable.pdf"

    reader = pypdf.PdfReader(str(input_pdf))
    total_pages = len(reader.pages)
    existing_texts, page_dims, pages_needing_ocr = [], [], []

    for idx, page in enumerate(reader.pages):
        txt = (page.extract_text() or "").strip()
        existing_texts.append(txt)
        page_dims.append((float(page.mediabox.width), float(page.mediabox.height)))
        if force_ocr or len(txt) < min_chars:
            pages_needing_ocr.append(idx)

    ocr_results: dict[int, Tuple[bytes, str, List[dict]]] = {}
    if pages_needing_ocr:
        worker_count = max(1, min(workers, len(pages_needing_ocr)))
        if worker_count == 1:
            for idx in pages_needing_ocr:
                w, h = page_dims[idx]
                _, pdf_bytes, txt, lines = ocr_single_page(str(input_pdf), idx, w, h, dpi, max_pixel_dim, mode, lang, oem)
                ocr_results[idx] = (pdf_bytes, txt, lines)
        else:
            with concurrent.futures.ProcessPoolExecutor(max_workers=worker_count) as pool:
                futs = {
                    pool.submit(ocr_single_page, str(input_pdf), idx, page_dims[idx][0], page_dims[idx][1], dpi, max_pixel_dim, mode, lang, oem): idx
                    for idx in pages_needing_ocr
                }
                for fut in concurrent.futures.as_completed(futs):
                    idx, pdf_bytes, txt, lines = fut.result()
                    ocr_results[idx] = (pdf_bytes, txt, lines)

    writer = pypdf.PdfWriter()
    page_reports: List[PageExtraction] = []
    all_page_texts: List[str] = []

    for idx in range(total_pages):
        w, h = page_dims[idx]
        if idx in ocr_results:
            pdf_bytes, txt, lines = ocr_results[idx]
            ocr_page = normalize_page_size(pypdf.PdfReader(io.BytesIO(pdf_bytes)).pages[0], w, h)
            writer.add_page(ocr_page)
            ocr_applied = True
        else:
            writer.add_page(reader.pages[idx])
            txt = existing_texts[idx]
            lines = [{"text": ln, "font_pt": 10.0, "tier": "callout", "in_title_block": False} for ln in txt.splitlines() if ln.strip()]
            ocr_applied = False

        all_page_texts.append(txt)
        sheets, tags, caps, csi = extract_hvac_entities(txt)
        cats, is_diag, title, tb_lines, sched_lines, tier_counts = classify_page(txt, sheets, w, h, lines)

        page_reports.append(
            PageExtraction(
                page_number=idx + 1,
                ocr_applied=ocr_applied,
                char_count=len(txt),
                categories=cats,
                is_any_diagram=is_diag,
                inferred_title=title,
                sheet_numbers=sheets,
                equipment_tags=tags,
                capacities=caps,
                csi_sections=csi,
                title_block_lines=tb_lines,
                schedule_lines=sched_lines,
                font_tier_counts=tier_counts,
            )
        )

    with open(out_pdf_path, "wb") as f:
        writer.write(f)

    if export_text:
        with open(output_dir / f"{input_pdf.stem}_text.txt", "w", encoding="utf-8") as f:
            for p, txt in zip(page_reports, all_page_texts):
                f.write(f"==================== PAGE {p.page_number} [{', '.join(p.categories).upper()}] ====================\n")
                f.write(txt + "\n\n")

    links = {
        "floorplans": [{"page": p.page_number, "sheets": p.sheet_numbers, "title": p.inferred_title} for p in page_reports if "floorplan" in p.categories],
        "control_diagrams": [{"page": p.page_number, "sheets": p.sheet_numbers, "title": p.inferred_title} for p in page_reports if "control_diagram" in p.categories],
        "sequence_of_operations": [{"page": p.page_number, "sheets": p.sheet_numbers, "title": p.inferred_title} for p in page_reports if "sequence_of_ops" in p.categories],
        "all_diagrams": [{"page": p.page_number, "sheets": p.sheet_numbers, "title": p.inferred_title} for p in page_reports if p.is_any_diagram],
        "schedules": [{"page": p.page_number, "sheets": p.sheet_numbers, "title": p.inferred_title} for p in page_reports if "schedule" in p.categories],
    }

    report = DocumentReport(
        source_file=str(input_pdf),
        output_pdf=str(out_pdf_path),
        total_pages=total_pages,
        pages_ocr_processed=len(pages_needing_ocr),
        pages_preserved=total_pages - len(pages_needing_ocr),
        links=links,
        unique_equipment_tags=sorted({t for p in page_reports for t in p.equipment_tags}),
        unique_csi_sections=sorted({c for p in page_reports for c in p.csi_sections}),
        unique_sheet_numbers=sorted({s for p in page_reports for s in p.sheet_numbers}),
        pages=page_reports,
    )

    if export_json:
        with open(output_dir / f"{input_pdf.stem}_extracted.json", "w", encoding="utf-8") as f:
            json.dump(asdict(report), f, indent=2)

    if export_csv:
        with open(output_dir / f"{input_pdf.stem}_extracted.csv", "w", newline="", encoding="utf-8") as f:
            fields = [
                "page_number", "categories", "inferred_title", "sheet_numbers",
                "equipment_tags", "capacities", "csi_sections", "title_block_lines", "schedule_rows_count",
            ]
            w_csv = csv.DictWriter(f, fieldnames=fields)
            w_csv.writeheader()
            for p in page_reports:
                w_csv.writerow({
                    "page_number": p.page_number,
                    "categories": "; ".join(p.categories),
                    "inferred_title": p.inferred_title,
                    "sheet_numbers": "; ".join(p.sheet_numbers),
                    "equipment_tags": "; ".join(p.equipment_tags),
                    "capacities": "; ".join(p.capacities),
                    "csi_sections": "; ".join(p.csi_sections),
                    "title_block_lines": " | ".join(p.title_block_lines),
                    "schedule_rows_count": len(p.schedule_lines),
                })

    if verbose:
        print(f"Saved searchable PDF -> {out_pdf_path}")
    return report


def collect_pdf_inputs(target: Path, recursive: bool) -> List[Path]:
    if target.is_file():
        if target.suffix.lower() != ".pdf":
            raise ValueError(f"Input file is not a PDF: {target}")
        return [target]
    if target.is_dir():
        pattern = "**/*.pdf" if recursive else "*.pdf"
        pdfs = sorted(p for p in target.glob(pattern) if not p.stem.endswith("_searchable"))
        if not pdfs:
            raise FileNotFoundError(f"No PDF files found in directory: {target}")
        return pdfs
    raise FileNotFoundError(f"Input path does not exist: {target}")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Make HVAC plans and specs searchable and extract classified links.")
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output-dir", type=Path, default=None)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--max-pixel-dim", type=int, default=6000)
    parser.add_argument("--mode", choices=("auto", "plan", "spec"), default="auto")
    parser.add_argument("--force-ocr", action="store_true")
    parser.add_argument("--min-chars", type=int, default=50)
    parser.add_argument("--lang", default="eng")
    parser.add_argument("--oem", type=int, default=3)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--export-text", action="store_true")
    parser.add_argument("--export-json", action="store_true")
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument("--tesseract-cmd", default=None)
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = args.tesseract_cmd
    elif shutil.which("tesseract") is None:
        print("Error: 'tesseract' not found in PATH.", file=sys.stderr)
        return 1

    for pdf_path in collect_pdf_inputs(args.input, recursive=args.recursive):
        process_pdf(
            input_pdf=pdf_path,
            output_dir=args.output_dir or pdf_path.parent,
            dpi=args.dpi,
            max_pixel_dim=args.max_pixel_dim,
            mode=args.mode,
            lang=args.lang,
            oem=args.oem,
            min_chars=args.min_chars,
            force_ocr=args.force_ocr,
            workers=args.workers,
            export_text=args.export_text,
            export_json=args.export_json,
            export_csv=args.export_csv,
            verbose=not args.quiet,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
