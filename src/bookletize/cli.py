"""Command line interface: `bookletize book.pdf -c 1-12 -c 13-30`."""
from __future__ import annotations

import argparse
import sys

from pypdf.errors import PyPdfError

from . import __version__
from .core import MM, PAPER, BookletError, format_summary, make_booklets

EPILOG = """examples:
  bookletize book.pdf                         whole PDF as one booklet
  bookletize book.pdf -r 5-40                 pages 5-40 only
  bookletize book.pdf -c 1-12 -c 13-30        two booklets, one file each
  bookletize book.pdf -c "1-5,9-12" -c 13-30  several ranges in one booklet
  bookletize ch1.pdf ch2.pdf                  one booklet per file
  bookletize book.pdf -c 1-12 -c 13-30 --merge   all booklets in one file

print: double-sided, flip on short edge, actual size (100%)
"""


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bookletize",
        description="Rearrange PDF pages so they print as foldable booklets.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("inputs", nargs="+", help="PDF file(s); several files = one booklet each")
    ap.add_argument("-o", "--output", help="output file (default: <input>_booklet.pdf)")
    ap.add_argument("-r", "--range", default="",
                    help="pages for a single booklet, e.g. 5-40 or 1-3,10-20")
    ap.add_argument("-c", "--chapter", action="append", default=[], metavar="RANGE",
                    help="pages for one booklet; repeat for each booklet")
    ap.add_argument("--merge", action="store_true",
                    help="put all booklets in one PDF (default: one PDF per booklet)")
    ap.add_argument("-s", "--signature", type=int, default=0, metavar="N",
                    help="split each booklet into stacks of N pages (multiple of 4)")
    ap.add_argument("--blank-start", type=int, default=0, metavar="N",
                    help="blank pages at the front of each booklet")
    ap.add_argument("--paper", choices=PAPER, default="a4")
    ap.add_argument("--margin", type=float, default=0, metavar="MM",
                    help="margin around each page, in millimetres")
    ap.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    tty = sys.stdout.isatty()

    def progress(n, done, total):
        if tty:
            end = "\n" if done == total else ""
            print(f"\rBook {n}: side {done}/{total}", end=end, flush=True)

    try:
        result = make_booklets(
            args.inputs,
            booklets=args.chapter,
            page_range=args.range,
            merge=args.merge,
            signature=args.signature,
            blank_start=args.blank_start,
            paper=args.paper,
            margin=args.margin * MM,
            output=args.output,
            progress=progress,
        )
    except (BookletError, PyPdfError, OSError) as e:
        # OSError: missing input, or output still open in a PDF viewer on Windows
        if tty:
            print()
        print(f"error: {e}", file=sys.stderr)
        return 2
    print()
    print(format_summary(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
