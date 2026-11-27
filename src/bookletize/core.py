"""
Core imposition logic shared by the command line tool and the GUI.

A booklet is a list of pages that is padded to a multiple of 4 and laid out
two pages per side on landscape sheets, so that the printed stack can be
folded in half and stapled along the fold.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.errors import PyPdfError
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    StreamObject,
)

PAPER = {  # portrait (width, height) in points
    "a4": (595.276, 841.890),
    "letter": (612.0, 792.0),
    "a3": (841.890, 1190.551),
}

MM = 72 / 25.4  # points per millimetre

# progress(booklet_number, sides_done, sides_total)
ProgressFn = Callable[[int, int, int], None]


class BookletError(ValueError):
    """Raised for invalid user input (bad ranges, bad options)."""


# --------------------------------------------------------------------------- #
# Page ordering
# --------------------------------------------------------------------------- #
def parse_ranges(spec: str, total: int) -> list[int]:
    """'1-3,7,10-' -> [0,1,2,6,9,10,...] (0-based). Empty spec = all pages."""
    if not spec or not spec.strip():
        return list(range(total))
    pages = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a_s, b_s = part.split("-", 1)
                a = int(a_s) if a_s.strip() else 1
                b = int(b_s) if b_s.strip() else total
            else:
                a = b = int(part)
        except ValueError:
            raise BookletError(f"Invalid range '{part}'") from None
        if not (1 <= a <= b <= total):
            raise BookletError(f"Invalid range '{part}' (PDF has {total} pages)")
        pages.extend(range(a - 1, b))
    if not pages:
        raise BookletError(f"Empty range '{spec}'")
    return pages


def booklet_order(n: int) -> list[tuple[int, int]]:
    """
    For n pages (n divisible by 4) return the list of sheet sides.
    Each side is (left_index, right_index), 0-based within the booklet.
    Sheet k front: (n-1-2k, 2k)   back: (2k+1, n-2-2k)
    """
    if n % 4:
        raise ValueError("n must be a multiple of 4")
    sides = []
    for k in range(n // 4):
        sides.append((n - 1 - 2 * k, 2 * k))
        sides.append((2 * k + 1, n - 2 - 2 * k))
    return sides


def split_signatures(pages: list, signature: int) -> list[list]:
    """Split one booklet's page list into signatures, each padded to a multiple of 4."""
    chunk = signature or len(pages)
    chunk = max(4, -(-chunk // 4) * 4)
    groups = [pages[i:i + chunk] for i in range(0, len(pages), chunk)] or [[]]
    groups[-1] = groups[-1] + [None] * ((-len(groups[-1])) % 4)
    return groups


# --------------------------------------------------------------------------- #
# PDF placement
# --------------------------------------------------------------------------- #
def page_matrix(src, x0: float, slot_w: float, slot_h: float, margin: float) -> Transformation:
    """Transformation that fits src (after its /Rotate) into the slot, centered."""
    box = src.cropbox
    w, h = float(box.width), float(box.height)
    rot = (src.rotation or 0) % 360

    # Move the crop box to the origin, then apply the page's display rotation
    # (/Rotate is clockwise; Transformation.rotate is counter-clockwise).
    t = Transformation().translate(-float(box.left), -float(box.bottom))
    if rot == 90:
        t = t.rotate(-90).translate(0, w)
        w, h = h, w
    elif rot == 180:
        t = t.rotate(180).translate(w, h)
    elif rot == 270:
        t = t.rotate(90).translate(h, 0)
        w, h = h, w

    avail_w, avail_h = slot_w - 2 * margin, slot_h - 2 * margin
    scale = min(avail_w / w, avail_h / h)
    tx = x0 + margin + (avail_w - w * scale) / 2
    ty = margin + (avail_h - h * scale) / 2
    return t.scale(scale).translate(tx, ty)


ANNOT_HIDDEN, ANNOT_PRINT = 2, 4


def _appearance(annot):
    """The normal appearance stream (unresolved reference) a printer would draw, or None."""
    flags = int(annot.get("/F", 0))
    if flags & ANNOT_HIDDEN or not flags & ANNOT_PRINT:
        return None
    ap = annot.get("/AP")
    if ap is None:
        return None
    ref = ap.get_object().get("/N")
    if ref is None:
        return None
    if not isinstance(ref.get_object(), StreamObject):  # one appearance per state (check boxes)
        states, state = ref.get_object(), annot.get("/AS")
        if state is None or state not in states:
            return None
        ref = states.raw_get(state)
    return ref


def _annotation_ops(writer: PdfWriter, src, xobjects: DictionaryObject) -> list:
    """
    Drawing commands that paint the page's printable annotations (highlights, comments,
    filled form fields) from their appearance streams, as a PDF viewer does when printing.
    The appearance streams are added to xobjects.
    """
    annots = src.get("/Annots")
    if annots is None:
        return []
    ops = []
    for i, aref in enumerate(annots.get_object()):
        annot = aref.get_object()
        ref = _appearance(annot) if isinstance(annot, DictionaryObject) else None
        if ref is None or "/Rect" not in annot:
            continue
        form = ref.get_object()
        x0, y0, x1, y1 = (float(v) for v in annot["/Rect"])
        rx0, rx1, ry0, ry1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        # PDF 32000 12.5.5: map the appearance BBox (after its /Matrix) onto /Rect
        a, b, c, d, e, f = (float(v) for v in form.get("/Matrix", [1, 0, 0, 1, 0, 0]))
        bx0, by0, bx1, by1 = (float(v) for v in form.get("/BBox", [0, 0, 0, 0]))
        corners = [(x * a + y * c + e, x * b + y * d + f) for x in (bx0, bx1) for y in (by0, by1)]
        tx0, tx1 = min(p[0] for p in corners), max(p[0] for p in corners)
        ty0, ty1 = min(p[1] for p in corners), max(p[1] for p in corners)
        if tx1 - tx0 <= 0 or ty1 - ty0 <= 0:
            continue
        sx, sy = (rx1 - rx0) / (tx1 - tx0), (ry1 - ry0) / (ty1 - ty0)
        copy = ref.clone(writer) if isinstance(ref, IndirectObject) else writer._add_object(form.clone(writer))
        name = f"/BkAnnot{i}"
        xobjects[NameObject(name)] = copy
        ops.append(f"q {sx:.6f} 0 0 {sy:.6f} {rx0 - tx0 * sx:.4f} {ry0 - ty0 * sy:.4f} cm {name} Do Q")
    return ops


def page_as_xobject(writer: PdfWriter, src):
    """
    Wrap a source page as a Form XObject in the writer.
    The page's drawing commands are copied as raw bytes (never parsed),
    which is much faster than merge_transformed_page for heavy pages.
    Printable annotations are drawn on top, as a viewer would print them.
    """
    contents = src.get("/Contents")
    data = b""
    if contents is not None:
        contents = contents.get_object()
        parts = contents if isinstance(contents, ArrayObject) else [contents]
        data = b"\n".join(p.get_object().get_data() for p in parts)

    res = src.get("/Resources")
    res = res.get_object().clone(writer) if res is not None else DictionaryObject()
    annot_xobjects = DictionaryObject()
    annot_ops = _annotation_ops(writer, src, annot_xobjects)
    if annot_ops:
        # q/Q keeps the page's graphics state from leaking into the annotations
        data = b"q\n" + data + b"\nQ\n" + "\n".join(annot_ops).encode()
        res = DictionaryObject(res)  # cloned resources may be shared with other pages
        xobjects = res.get("/XObject")
        xobjects = xobjects.get_object() if xobjects is not None else DictionaryObject()
        xobjects = DictionaryObject({**xobjects, **annot_xobjects})
        res[NameObject("/XObject")] = xobjects

    box = src.cropbox
    xobj = DecodedStreamObject()
    xobj.set_data(data)
    xobj.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject(
            [FloatObject(float(v)) for v in (box.left, box.bottom, box.right, box.top)]
        ),
        NameObject("/Resources"): res,
    })
    return writer._add_object(xobj.flate_encode())


def impose(writer: PdfWriter, groups, sheet_w: float, sheet_h: float, margin: float,
           on_side: Optional[Callable[[int, int], None]] = None) -> None:
    """Append the imposed sheet sides for a list of signatures to writer."""
    slot_w = sheet_w / 2
    total = sum(len(g) for g in groups) // 2
    done = 0
    for group in groups:
        for left, right in booklet_order(len(group)):
            sheet = writer.add_blank_page(width=sheet_w, height=sheet_h)
            xobjects = DictionaryObject()
            ops = []
            for idx, x0 in ((left, 0), (right, slot_w)):
                if group[idx] is None:
                    continue
                name = f"/P{len(xobjects)}"
                xobjects[NameObject(name)] = page_as_xobject(writer, group[idx])
                a, b, c, d, e, f = page_matrix(group[idx], x0, slot_w, sheet_h, margin).ctm
                ops.append(f"q {a:.6f} {b:.6f} {c:.6f} {d:.6f} {e:.4f} {f:.4f} cm {name} Do Q")
            sheet[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
            stream = StreamObject()
            stream.set_data("\n".join(ops).encode())
            sheet[NameObject("/Contents")] = writer._add_object(stream)
            done += 1
            if on_side:
                on_side(done, total)


# --------------------------------------------------------------------------- #
# High level API
# --------------------------------------------------------------------------- #
def open_pdf(path: str) -> PdfReader:
    """Open a PDF, turning unreadable, locked or empty files into a BookletError."""
    name = os.path.basename(path)
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise BookletError(f"{name} is protected by a password; remove it first")
        count = len(reader.pages)
    except PyPdfError as e:
        raise BookletError(f"Cannot read {name}: {e}") from None
    if not count:
        raise BookletError(f"{name} has no pages")
    return reader


@dataclass
class BookletInfo:
    number: int
    source: str        # human readable description of the source pages
    pages: int         # content pages
    blank: int         # blank pages added (incl. --blank-start)
    sheets: int        # sheets of paper
    stacks: int        # signatures
    location: str      # output file name, or "sheet a-b" when merged


@dataclass
class Result:
    booklets: list[BookletInfo] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    signature: int = 0

    @property
    def total_sheets(self) -> int:
        return sum(b.sheets for b in self.booklets)


def make_booklets(
    inputs: Sequence[str],
    booklets: Sequence[str] = (),
    page_range: str = "",
    merge: bool = False,
    signature: int = 0,
    blank_start: int = 0,
    paper: str = "a4",
    margin: float = 0.0,
    output: Optional[str] = None,
    progress: Optional[ProgressFn] = None,
) -> Result:
    """
    Create booklet PDF(s).

    inputs      one or more PDF paths. Several paths = one booklet per file.
    booklets    page range specs for a single input, one booklet each
                (e.g. ["262-269,273-287", "315-329"]). Empty = one booklet.
    page_range  page range for the single booklet (when booklets is empty),
                or applied to every file when several inputs are given.
    merge       put all booklets in one output PDF.
    signature   pages per signature (multiple of 4), 0 = off.
    blank_start blank pages inserted at the front of every booklet.
    paper       key of PAPER.
    margin      margin around each page, in points.
    output      output path (default: <first input>_booklet.pdf). When several
                files are written, _book1, _book2... are appended to it.
    progress    callback(booklet_number, sides_done, sides_total).
    """
    inputs = list(inputs)
    booklets = [b for b in booklets if b and b.strip()]
    if not inputs:
        raise BookletError("No input PDF given")
    if signature < 0 or signature % 4:
        raise BookletError("Signature size must be a multiple of 4")
    if blank_start < 0:
        raise BookletError("Blank pages must be 0 or more")
    if booklets and page_range:
        raise BookletError("Use either a single page range or several booklets, not both")
    if booklets and len(inputs) > 1:
        raise BookletError("Several booklets from page ranges need a single input PDF")
    if paper not in PAPER:
        raise BookletError(f"Unknown paper size '{paper}'")
    pw, ph = PAPER[paper]
    sheet_w, sheet_h = ph, pw  # landscape sheet
    if margin < 0:
        raise BookletError("Margin must be 0 or more")
    if 2 * margin >= min(sheet_w / 2, sheet_h):
        raise BookletError(f"Margin is too large for {paper.upper()} paper")

    # Build the list of booklets: (file_tag, source_label, [PageObject, ...])
    plan = []
    if len(inputs) > 1:
        tags = set()
        for path in inputs:
            r = open_pdf(path)
            stem = tag = os.path.splitext(os.path.basename(path))[0]
            k = 1
            while tag.lower() in tags:  # same file name in different folders
                k += 1
                tag = f"{stem}_{k}"
            tags.add(tag.lower())
            label = os.path.basename(path) + (f" p.{page_range}" if page_range else "")
            plan.append((tag, label, [r.pages[i] for i in parse_ranges(page_range, len(r.pages))]))
    else:
        r = open_pdf(inputs[0])
        specs = booklets or [page_range]
        for n, spec in enumerate(specs, 1):
            label = (", ".join(s.strip() for s in spec.split(",") if s.strip())
                     if spec else f"1-{len(r.pages)}")
            plan.append((f"book{n}", label, [r.pages[i] for i in parse_ranges(spec, len(r.pages))]))

    base = output or os.path.splitext(inputs[0])[0] + "_booklet.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"

    split = len(plan) > 1 and not merge
    writer = None if split else PdfWriter()
    result = Result(signature=signature)
    sheet_no = 0
    for n, (tag, label, pages) in enumerate(plan, 1):
        content = len(pages)
        pages = [None] * blank_start + pages
        groups = split_signatures(pages, signature)
        slots = sum(len(g) for g in groups)
        sheets = slots // 4
        cb = (lambda d, t, n=n: progress(n, d, t)) if progress else None
        if split:
            w = PdfWriter()
            impose(w, groups, sheet_w, sheet_h, margin, cb)
            out = os.path.splitext(base)[0] + f"_{tag}.pdf"
            with open(out, "wb") as f:
                w.write(f)
            result.outputs.append(out)
            where = os.path.basename(out)
        else:
            impose(writer, groups, sheet_w, sheet_h, margin, cb)
            a, b = sheet_no + 1, sheet_no + sheets
            where = f"sheet {a}" if a == b else f"sheet {a}-{b}"
        sheet_no += sheets
        result.booklets.append(BookletInfo(n, label, content, slots - content,
                                           sheets, len(groups), where))

    if not split:
        with open(base, "wb") as f:
            writer.write(f)
        result.outputs.append(base)
    return result


def format_summary(result: Result) -> str:
    """Render the result as a plain-text table."""
    head = ["#", "Source pages", "Pages", "Blank", "Sheets", "Stacks", "Location"]
    rows = [[str(b.number), b.source, str(b.pages), f"+{b.blank}", str(b.sheets),
             str(b.stacks), b.location] for b in result.booklets]
    right = {0, 2, 3, 4, 5}
    if not result.signature:
        head.pop(5)
        right = {0, 2, 3, 4}
        for row in rows:
            row.pop(5)
    widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(len(head))]

    def line(cells):
        return "  ".join(c.rjust(w) if i in right else c.ljust(w)
                         for i, (c, w) in enumerate(zip(cells, widths))).rstrip()

    out = [line(head), "  ".join("-" * w for w in widths)]
    out += [line(r) for r in rows]
    out.append("")
    out.append(f"Total   {len(result.booklets)} booklet(s), {result.total_sheets} sheet(s) of paper")
    out += [f"Output  {o}" for o in result.outputs]
    out.append("Print   double-sided, FLIP ON SHORT EDGE, actual size (100%)")
    return "\n".join(out)
