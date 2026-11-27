"""bookletize: turn PDF pages into foldable, saddle-stitched booklets."""
__version__ = "1.0.1"

from .core import BookletError, make_booklets, format_summary, booklet_order, parse_ranges  # noqa: E402,F401
