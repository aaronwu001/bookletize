import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from bookletize import BookletError, booklet_order, make_booklets, parse_ranges
from bookletize.core import split_signatures


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
