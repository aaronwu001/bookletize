import os

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    NameObject,
    NumberObject,
)

from bookletize import BookletError, booklet_order, cli, make_booklets, parse_ranges
from bookletize.core import MM, split_signatures


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def make_pdf(path, n, rotate=None):
    """PDF whose page i (1-based) has the content stream '% PAGE i'."""
    w = PdfWriter()
    for i in range(1, n + 1):
        page = w.add_blank_page(width=595, height=842)
        s = DecodedStreamObject()
        s.set_data(f"% PAGE {i}\n".encode())
        page[NameObject("/Contents")] = w._add_object(s)
        if rotate:
            page.rotation = rotate
    with open(path, "wb") as f:
        w.write(f)
    return str(path)


def sides(path):
    """Return [(left, right), ...] page numbers per sheet side (None = blank)."""
    out = []
    for page in PdfReader(path).pages:
        xobjs = page["/Resources"]["/XObject"]
        ops = page.get_contents().get_data().decode()
        found = []
        for line in ops.splitlines():
            name = line.split()[-3]
            x = float(line.split()[5])
            data = xobjs[name].get_object().get_data().decode()
            found.append((x, int(data.split()[-1])))
        left = next((p for x, p in found if x < 421), None)
        right = next((p for x, p in found if x >= 421), None)
        out.append((left, right))
    return out


# --------------------------------------------------------------------------- #
# pure functions
# --------------------------------------------------------------------------- #
def test_booklet_order_8():
    assert booklet_order(8) == [(7, 0), (1, 6), (5, 2), (3, 4)]


@pytest.mark.parametrize("n", [4, 8, 12, 48, 400])
def test_booklet_order_is_permutation(n):
    flat = [p for side in booklet_order(n) for p in side]
    assert sorted(flat) == list(range(n))


def test_booklet_order_rejects_non_multiple_of_4():
    with pytest.raises(ValueError):
        booklet_order(6)


def test_parse_ranges():
    assert parse_ranges("1-3,7,9-", 10) == [0, 1, 2, 6, 8, 9]
    assert parse_ranges("", 3) == [0, 1, 2]
    assert parse_ranges(" 2 - 3 , 5 ", 5) == [1, 2, 4]


@pytest.mark.parametrize("bad", ["0-3", "5-2", "11", "a-b", ","])
def test_parse_ranges_invalid(bad):
    with pytest.raises(BookletError):
        parse_ranges(bad, 10)


def test_split_signatures_pads_each_stack():
    groups = split_signatures(list(range(10)), 8)
    assert [len(g) for g in groups] == [8, 4]
    assert groups[1] == [8, 9, None, None]


# --------------------------------------------------------------------------- #
# end to end
# --------------------------------------------------------------------------- #
def test_single_booklet_order(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 8)
    res = make_booklets([src])
    assert sides(res.outputs[0]) == [(8, 1), (2, 7), (6, 3), (4, 5)]


def test_padding_with_blanks(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 5)
    res = make_booklets([src])
    assert res.booklets[0].blank == 3
    assert sides(res.outputs[0]) == [(None, 1), (2, None), (None, 3), (4, 5)]


def test_several_booklets_split_files(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 20)
    res = make_booklets([src], booklets=["1-3,6", "10-13"])
    assert len(res.outputs) == 2
    assert sides(res.outputs[0]) == [(6, 1), (2, 3)]
    assert sides(res.outputs[1]) == [(13, 10), (11, 12)]


def test_merge_keeps_booklets_separate(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 20)
    res = make_booklets([src], booklets=["1-5", "6-9"], merge=True)
    assert len(res.outputs) == 1
    assert [b.location for b in res.booklets] == ["sheet 1-2", "sheet 3"]
    assert sides(res.outputs[0])[4:] == [(9, 6), (7, 8)]


def test_multiple_files(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 4)
    b = make_pdf(tmp_path / "b.pdf", 2)
    res = make_booklets([a, b], output=str(tmp_path / "out.pdf"))
    assert [o.rsplit("_", 1)[-1] for o in res.outputs] == ["a.pdf", "b.pdf"]


def test_rotated_pages_fit_slot(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 4, rotate=90)
    res = make_booklets([src])
    assert res.total_sheets == 1


def test_invalid_signature(tmp_path):
    src = make_pdf(tmp_path / "a.pdf", 4)
    with pytest.raises(BookletError):
        make_booklets([src], signature=6)


@pytest.mark.parametrize("mm", [-1, 106, 500])
def test_margin_out_of_range(tmp_path, mm):
    # half an A4 landscape sheet is 148.5 mm wide, so 106 mm per side leaves nothing
    src = make_pdf(tmp_path / "a.pdf", 4)
    with pytest.raises(BookletError, match="Margin"):
        make_booklets([src], margin=mm * MM)


def test_same_file_name_in_two_folders(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a = make_pdf(tmp_path / "x" / "ch.pdf", 4)
    b = make_pdf(tmp_path / "y" / "ch.pdf", 8)
    res = make_booklets([a, b], output=str(tmp_path / "out.pdf"))
    assert [os.path.basename(o) for o in res.outputs] == ["out_ch.pdf", "out_ch_2.pdf"]
    assert [len(PdfReader(o).pages) for o in res.outputs] == [2, 4]


def test_empty_pdf(tmp_path):
    path = tmp_path / "empty.pdf"
    PdfWriter().write(str(path))
    with pytest.raises(BookletError, match="no pages"):
        make_booklets([str(path)])


def test_not_a_pdf(tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_text("hello")
    with pytest.raises(BookletError, match="Cannot read"):
        make_booklets([str(path)])


def test_encrypted_without_user_password_opens(tmp_path):
    # Many PDFs only carry an owner password (print/copy restrictions) and open freely.
    w = PdfWriter(clone_from=make_pdf(tmp_path / "a.pdf", 4))
    w.encrypt(user_password="", owner_password="owner", algorithm="AES-256")
    path = str(tmp_path / "enc.pdf")
    w.write(path)
    res = make_booklets([path])
    assert sides(res.outputs[0]) == [(4, 1), (2, 3)]


def test_encrypted_with_user_password(tmp_path):
    w = PdfWriter(clone_from=make_pdf(tmp_path / "a.pdf", 4))
    w.encrypt(user_password="secret", owner_password="owner", algorithm="AES-256")
    path = str(tmp_path / "enc.pdf")
    w.write(path)
    with pytest.raises(BookletError, match="password"):
        make_booklets([path])


def test_cli_reports_errors_without_traceback(tmp_path, capsys):
    path = tmp_path / "bad.pdf"
    path.write_text("hello")
    assert cli.main([str(path)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: Cannot read bad.pdf") and "Traceback" not in err


# --------------------------------------------------------------------------- #
# annotations (highlights, comments, filled form fields)
# --------------------------------------------------------------------------- #
def add_annot(writer, page, text, flags, rect=(100, 100, 300, 200), matrix=None):
    ap = DecodedStreamObject()
    ap.set_data(f"% {text}\n".encode())
    ap.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (0, 0, 50, 25)]),
    })
    if matrix:
        ap[NameObject("/Matrix")] = ArrayObject([FloatObject(v) for v in matrix])
    annot = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/FreeText"),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/F"): NumberObject(flags),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): writer._add_object(ap)}),
    })
    annots = page.get("/Annots")
    if annots is None:
        annots = ArrayObject()
        page[NameObject("/Annots")] = annots
    annots.append(writer._add_object(annot))


def placed_annots(path):
    """{source page number: [(annotation text, cm matrix), ...]} for every placed page."""
    out = {}
    for sheet in PdfReader(path).pages:
        for xref in sheet["/Resources"]["/XObject"].values():
            form = xref.get_object()
            lines = form.get_data().decode().splitlines()
            page_no = int(next(ln for ln in lines if ln.startswith("% PAGE")).split()[-1])
            found = []
            for ln in lines:
                if ln.endswith(" Do Q"):
                    parts = ln.split()
                    data = form["/Resources"]["/XObject"][parts[-3]].get_object().get_data()
                    found.append((data.decode().split()[-1], [float(v) for v in parts[1:7]]))
            out[page_no] = found
    return out


def test_printable_annotations_are_drawn(tmp_path):
    w = PdfWriter(clone_from=make_pdf(tmp_path / "a.pdf", 4))
    add_annot(w, w.pages[0], "NOTE1", flags=4)
    add_annot(w, w.pages[1], "HIDDEN", flags=4 | 2)
    add_annot(w, w.pages[1], "SCREENONLY", flags=0)
    add_annot(w, w.pages[2], "SCALED", flags=4, rect=(0, 0, 100, 100), matrix=(1, 0, 0, 1, 10, 20))
    path = str(tmp_path / "annot.pdf")
    w.write(path)
    res = make_booklets([path])
    placed = placed_annots(res.outputs[0])
    assert placed[1] == [("NOTE1", [4, 0, 0, 4, 100, 100])]   # 50x25 box -> 200x100 rect
    assert placed[2] == [] and placed[4] == []
    assert placed[3] == [("SCALED", [2, 0, 0, 4, -20, -80])]  # bbox moved by /Matrix first


def test_annotations_with_shared_resources(tmp_path):
    # Pages that share one /Resources object must each keep their own annotation.
    w = PdfWriter(clone_from=make_pdf(tmp_path / "a.pdf", 4))
    shared = w._add_object(DictionaryObject())
    for i, page in enumerate(w.pages, 1):
        page[NameObject("/Resources")] = shared
        add_annot(w, page, f"A{i}", flags=4)
    path = str(tmp_path / "shared.pdf")
    w.write(path)
    placed = placed_annots(make_booklets([path]).outputs[0])
    assert {p: [t for t, _ in a] for p, a in placed.items()} == {1: ["A1"], 2: ["A2"], 3: ["A3"], 4: ["A4"]}
