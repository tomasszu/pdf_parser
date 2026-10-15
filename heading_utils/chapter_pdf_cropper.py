"""  
Crops a chapter's exact page range out of the source PDF, trimming the  
start and end pages down to the chapter's actual content bounds (so  
headers/footers and neighboring chapter content aren't included).

Chapter content bounds are read from a heading-adjusted chapter JSON file  
(as produced by HeadingsAdjuster.adjust_chapters), which must contain:

    - at least one heading-type block, the first of which has a "bbox"  
      (Docling bottom-left-origin coordinates) marking the chapter's start  
    - the LAST heading-type block in document order must additionally  
      have "section_end_bbox" / "section_end_page", marking where the  
      chapter's content run ends (either the start of the next chapter's  
      first heading, or the bottom of its own page as a fallback)

Two-column ("academic") PDF support  
------------------------------------  
In a two-column layout, reading order on a page is: all of column 1  
top-to-bottom, then all of column 2 top-to-bottom. A chapter boundary  
heading that sits mid-column therefore implies an L-shaped "keep"  
region, e.g. for a chapter START whose heading is partway down column 1:

    keep = (column 1, from heading's top down to the page bottom)  
         + (column 2, in full)

Since a PDF cropbox can only express a single rectangle, an L-shaped  
keep region is realized as TWO output pages -- one per rectangle --  
inserted in correct reading order, both still showing the *same*  
physical source page, just cropped differently.

We distinguish two kinds of headings:

    - "full-width" headings (e.g. a chapter title that spans the whole  
      page, with two-column body text below it): handled exactly like  
      the original single-column logic -- one simple top/bottom cut  
      across the full page width.  
    - "narrow"/column-confined headings (e.g. a sub-heading that lives  
      inside one of the two columns): handled with the L-shaped logic  
      above.

A heading counts as "narrow" when its bbox width is less than  
`full_width_threshold` (default 0.65) of the page width. The column  
split point defaults to the page's horizontal midpoint  
(`column_split_frac`, default 0.5) -- i.e. we assume a standard  
symmetric two-column layout.

Requires PyMuPDF:  pip install PyMuPDF  
"""

import logging  
from pathlib import Path

import fitz  # PyMuPDF

_log = logging.getLogger(__name__)

HEADING_TYPE = "heading"


def _collect_heading_kids(chapter_json):  
    sections = chapter_json.get("document", {}).get("sections", [])  
    return [  
        kid  
        for section in sections  
        for kid in section.get("kids", [])  
        if kid.get("type") == HEADING_TYPE  
    ]


def _bottomleft_to_fitz_y(y_bottomleft, page_height):  
    """  
    Converts a y-coordinate from Docling's BOTTOMLEFT-origin space (y=0 at  
    the bottom of the page, increasing upward) into PyMuPDF's TOPLEFT-  
    origin space (y=0 at the top of the page, increasing downward).  
    """  
    return page_height - y_bottomleft


def _is_narrow_bbox(bbox, page_width, full_width_threshold):  
    """True if `bbox` is confined to roughly one column, not the full page width."""  
    width = bbox.get("r", page_width) - bbox.get("l", 0)  
    return width < full_width_threshold * page_width


def _column_side(bbox, mid_x):  
    """Which column `bbox` sits in, based on its horizontal center vs `mid_x`."""  
    l = bbox.get("l", 0)  
    r = bbox.get("r", mid_x * 2)  
    center = (l + r) / 2.0  
    return "left" if center < mid_x else "right"


def _rects_for_boundary_page(  
    page_width,  
    page_height,  
    start_bbox=None,  
    end_bbox=None,  
    padding=0.0,  
    column_split_frac=0.5,  
    full_width_threshold=0.65,  
):  
    """  
    Computes the ordered list of keep-rectangles for a chapter boundary  
    page (the first page of the chapter, the last page, or both at once  
    for a single-page chapter).

    `start_bbox` (if given) is the chapter's first heading's bbox: keep  
    everything in reading order FROM this heading ONWARD on the page.

    `end_bbox` (if given) is the chapter's section_end_bbox: keep  
    everything in reading order BEFORE this point on the page.

    Returns a list of fitz.Rect, in correct reading order, each meant to  
    become one output page (all backed by the same source page, just  
    cropped differently). A single-rectangle result means no page  
    duplication is needed for this boundary.  
    """  
    if start_bbox is None and end_bbox is None:  
        raise ValueError("Must supply at least one of start_bbox/end_bbox")

    mid_x = page_width * column_split_frac

    def top_y(bbox):  
        y = _bottomleft_to_fitz_y(bbox.get("t", page_height), page_height)  
        return max(0.0, y - padding)

    def bottom_y(bbox):  
        y = _bottomleft_to_fitz_y(bbox.get("t", 0), page_height)  
        return min(page_height, y + padding)

    # --- Start-only page (first page of a multi-page chapter) ----------  
    if start_bbox is not None and end_bbox is None:  
        if not _is_narrow_bbox(start_bbox, page_width, full_width_threshold):  
            y = top_y(start_bbox)  
            return [fitz.Rect(0, y, page_width, page_height)]

        side = _column_side(start_bbox, mid_x)  
        y = top_y(start_bbox)  
        if side == "left":  
            return [  
                fitz.Rect(0, y, mid_x, page_height),       # rest of col 1  
                fitz.Rect(mid_x, 0, page_width, page_height),  # all of col 2  
            ]  
        else:  # heading already in the 2nd column  
            return [fitz.Rect(mid_x, y, page_width, page_height)]

    # --- End-only page (last page of a multi-page chapter) -------------  
    if end_bbox is not None and start_bbox is None:  
        if not _is_narrow_bbox(end_bbox, page_width, full_width_threshold):  
            y = bottom_y(end_bbox)  
            return [fitz.Rect(0, 0, page_width, y)]

        side = _column_side(end_bbox, mid_x)  
        y = bottom_y(end_bbox)  
        if side == "left":  
            return [fitz.Rect(0, 0, mid_x, y)]  
        else:  # next chapter's heading is in col 2 -> col 1 is kept whole  
            return [  
                fitz.Rect(0, 0, mid_x, page_height),  
                fitz.Rect(mid_x, 0, page_width, y),  
            ]

    # --- Single-page chapter: both constraints apply to the same page --  
    start_narrow = _is_narrow_bbox(start_bbox, page_width, full_width_threshold)  
    end_narrow = _is_narrow_bbox(end_bbox, page_width, full_width_threshold)  
    y_top = top_y(start_bbox)  
    y_bottom = bottom_y(end_bbox)

    if not start_narrow and not end_narrow:  
        return [fitz.Rect(0, y_top, page_width, y_bottom)]

    s_side = _column_side(start_bbox, mid_x) if start_narrow else None  
    e_side = _column_side(end_bbox, mid_x) if end_narrow else None

    if start_narrow and end_narrow:  
        if s_side == "left" and e_side == "left":  
            return [fitz.Rect(0, y_top, mid_x, y_bottom)]  
        if s_side == "left" and e_side == "right":  
            return [  
                fitz.Rect(0, y_top, mid_x, page_height),  
                fitz.Rect(mid_x, 0, page_width, y_bottom),  
            ]  
        if s_side == "right" and e_side == "right":  
            return [fitz.Rect(mid_x, y_top, page_width, y_bottom)]  
        # s_side == "right" and e_side == "left": end precedes start in  
        # reading order -- shouldn't normally happen. Fall back to  
        # something conservative rather than raising.  
        _log.warning(  
            "Unexpected column order on single-page chapter: start "  
            "heading in right column but end heading in left column."  
        )  
        return [fitz.Rect(mid_x, y_top, page_width, page_height)]

    if start_narrow and not end_narrow:  
        # start confined to a column; end is a full-width cut  
        if s_side == "left":  
            return [  
                fitz.Rect(0, y_top, mid_x, y_bottom),  
                fitz.Rect(mid_x, 0, page_width, y_bottom),  
            ]  
        return [fitz.Rect(mid_x, y_top, page_width, y_bottom)]

    # not start_narrow and end_narrow: start is a full-width cut; end  
    # confined to a column  
    if e_side == "left":  
        return [fitz.Rect(0, y_top, mid_x, y_bottom)]  
    return [  
        fitz.Rect(0, y_top, mid_x, page_height),  
        fitz.Rect(mid_x, y_top, page_width, y_bottom),  
    ]


def _insert_cropped_copy(out, src, page_idx, rect):  
    """Appends one copy of `src[page_idx]` to `out`, cropped to `rect`."""  
    out.insert_pdf(src, from_page=page_idx, to_page=page_idx)  
    new_page = out[-1]  
    clipped = rect & new_page.rect  # keep within page bounds  
    new_page.set_cropbox(clipped)  
    return new_page


def crop_chapter_pdf(  
    pdf_path,  
    chapter_json,  
    outpath,  
    padding=0.0,  
    column_split_frac=0.5,  
    full_width_threshold=0.65,  
):  
    """  
    Crops the source PDF at `pdf_path` down to just the page range and  
    content bounds spanned by the chapter described in `chapter_json`,  
    and saves the result to `outpath`.

    The chapter's start boundary is the bbox/page of its first heading  
    block; its end boundary is the section_end_bbox/section_end_page of  
    its last heading block (set by HeadingsAdjuster). Interior pages (if  
    the chapter spans more than two pages) are kept in full; only the  
    first and last page of the range are trimmed.

    Two-column support: if a boundary heading is confined to one column  
    (see `full_width_threshold`), the resulting "keep" region on that  
    page can be L-shaped (part of one column plus all of the other, in  
    correct reading order). Since a PDF page/cropbox is rectangular, this  
    is realized by emitting that boundary page as TWO output pages, each  
    a differently-cropped copy of the same source page, in reading  
    order. Full-width headings (e.g. chapter titles spanning both  
    columns) are unaffected and behave exactly like the original  
    single-column logic.

    Args:  
        padding: Expands the vertical crop line(s) by this many PDF  
            points, to avoid clipping glyph ascenders/descenders right  
            at a boundary. Defaults to 0.  
        column_split_frac: Fraction of page width marking the boundary  
            between column 1 and column 2, for two-column pages.  
            Defaults to 0.5 (symmetric columns).  
        full_width_threshold: A boundary heading is treated as  
            column-confined ("narrow") when its bbox width is less than  
            this fraction of the page width; otherwise it's treated as  
            spanning the full page. Defaults to 0.65.

    Returns:  
        Path: the path the cropped PDF was written to.  
    """  
    heading_kids = _collect_heading_kids(chapter_json)  
    if not heading_kids:  
        raise ValueError("Chapter JSON has no heading-type blocks to derive crop bounds from")

    first = heading_kids[0]  
    last = heading_kids[-1]

    start_page_no = first.get("page")  
    start_bbox = first.get("bbox")  
    end_page_no = last.get("section_end_page")  
    end_bbox = last.get("section_end_bbox")

    if start_page_no is None or start_bbox is None:  
        raise ValueError(f"Chapter's first heading is missing page/bbox: {first}")  
    if end_page_no is None or end_bbox is None:  
        raise ValueError(f"Chapter's last heading is missing section_end_page/bbox: {last}")

    src = fitz.open(str(pdf_path))

    # PDF page numbers from Docling are assumed 1-indexed; PyMuPDF is  
    # 0-indexed.  
    start_idx = start_page_no - 1  
    end_idx = end_page_no - 1

    if start_idx < 0 or end_idx >= len(src) or start_idx > end_idx:  
        src.close()  
        raise ValueError(  
            f"Invalid page range for cropping: start_page={start_page_no}, "  
            f"end_page={end_page_no}, document has {len(src)} page(s)"  
        )

    out = fitz.open()  
    single_page = (start_idx == end_idx)

    rect_kwargs = dict(  
        padding=padding,  
        column_split_frac=column_split_frac,  
        full_width_threshold=full_width_threshold,  
    )

    if single_page:  
        page = src[start_idx]  
        rects = _rects_for_boundary_page(  
            page.rect.width, page.rect.height, start_bbox, end_bbox, **rect_kwargs  
        )  
        for r in rects:  
            _insert_cropped_copy(out, src, start_idx, r)  
    else:  
        start_page = src[start_idx]  
        start_rects = _rects_for_boundary_page(  
            start_page.rect.width, start_page.rect.height, start_bbox, None, **rect_kwargs  
        )  
        for r in start_rects:  
            _insert_cropped_copy(out, src, start_idx, r)

        if end_idx - start_idx > 1:  
            out.insert_pdf(src, from_page=start_idx + 1, to_page=end_idx - 1)

        end_page = src[end_idx]  
        end_rects = _rects_for_boundary_page(  
            end_page.rect.width, end_page.rect.height, None, end_bbox, **rect_kwargs  
        )  
        for r in end_rects:  
            _insert_cropped_copy(out, src, end_idx, r)

    outpath = Path(outpath)  
    outpath.parent.mkdir(parents=True, exist_ok=True)  
    out.save(str(outpath))

    out.close()  
    src.close()

    _log.info("Saved cropped chapter PDF to %s", outpath)  
    return outpath