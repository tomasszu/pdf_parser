import logging  
import re  
from pathlib import Path

from utils import (  
    normalize,  
    load_json,  
    save_json,  
    find_file_by_keyword,  
    iter_docling_page_files,  
)  
from heading_utils.docling_headings_mapping import map_headings  
from heading_utils.chapter_pdf_cropper import crop_chapter_pdf
from heading_utils.same_level_headings_extractor import SameLevelHeadingsExtractor

_log = logging.getLogger(__name__)


class HeadingsAdjuster:  
    """  
    Resolves the exact bbox for every heading-type block in already-split  
    NuExtract chapter files (e.g. chapters/02_methods.json) by cross-  
    referencing them against the Docling per-page parse of the same PDF.

    Besides a heading's own ("start") bbox, this also resolves a  
    "section_end_bbox" / "section_end_page" for each heading, marking where  
    that heading's content run ends:

        - for any heading that is NOT the last heading in its own chapter  
          file, the end boundary is simply the START of the next heading  
          in that SAME chapter's own (already correctly ordered) heading  
          list -- no cross-file lookup needed.  
        - for the LAST heading in a chapter file, the end boundary is the  
          START of the first heading in the NEXT chapter file on disk (by  
          true file order in chapters_dir), regardless of whether that  
          next chapter was itself requested for adjustment. If there is no  
          next chapter file at all, it falls back to the bottom of that  
          heading's own page.

    This is anchored to heading text -- NOT to the chapter's last paragraph  
    block -- since paragraph text is far more prone to normalization  
    mismatches (line wraps, hyphenation, inline citations, etc.) than  
    heading text.

    Document order for "what comes next" is determined from the actual  
    chapter files present on disk in `chapters_dir` (sorted by their  
    leading numeric prefix, e.g. 01_introduction.json, 02_methods.json,  
    ...) -- NOT from the list of chapter names passed to adjust_chapters().  
    That list only controls which chapters get extracted/adjusted; the  
    on-disk file order is the source of truth for sequencing.

    Requested chapter names that don't exist on disk (e.g. a document with  
    no clearly split "results" chapter) are skipped with a warning rather  
    than raising, since a batch call may be made generically across many  
    documents.

    If `pdf_path` is provided, adjust_chapters() will also produce a  
    cropped PDF per processed chapter -- spanning from the chapter's first  
    heading to its last heading's section_end boundary -- saved alongside  
    the adjusted JSON with the same filename stem (e.g.  
    02_methods.json -> 02_methods.pdf).

    Before bbox resolution happens, "weak headings" -- paragraph blocks  
    whose *entire* content is a single bold span (e.g. "**Patient  
    Selection.**" and nothing else) -- are detected and materialized into  
    real heading-type blocks with an assigned `level`:

        - if the nearest preceding heading was a strong (real) heading,  
          the weak heading is assigned that heading's level - 1  
        - if the nearest preceding heading was itself a weak heading, the  
          weak heading copies that heading's level  
        - if there is no preceding heading at all, it defaults to level 1

    Note: a paragraph that merely *starts* with bold text but has further  
    content following it is NOT a weak heading -- only blocks that are  
    bold text exclusively qualify.  
    """

    HEADING_TYPE = "heading"  
    PARAGRAPH_TYPE = "paragraph"

    # Matches ONLY when the entire block content (aside from surrounding  
    # whitespace) is a single bold span -- nothing before or after it.  
    BOLD_ONLY_RE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")

    # Matches a leading and/or trailing run of markdown emphasis markers  
    # (*, **, _, __, or combinations) wrapping a heading's content, e.g.  
    # "*Some Heading*" or "**Some Heading**". Docling's own node text never  
    # contains these markers, so they must be stripped before comparing a  
    # chapter heading's content against Docling text, or an otherwise  
    # perfect match will silently fail (leaving bbox = None).  
    MD_EMPHASIS_RE = re.compile(r"^[\*_]+|[\*_]+$")

    # Matches a leading integer in a chapter filename stem, e.g.  
    # "02_methods" -> 2, used to recover true document order on disk.  
    LEADING_NUM_RE = re.compile(r"^(\d+)")

    def __init__(  
        self,  
        chapters_dir,  
        docling_pages_dir,  
        outputs_path=None,  
        pdf_path=None,  
        same_level_extractor=None,  
    ):  
        self.chapters_dir = Path(chapters_dir)  
        self.docling_pages_dir = Path(docling_pages_dir)  
        self.outputs_path = Path(outputs_path) if outputs_path else None  
        self.pdf_path = Path(pdf_path) if pdf_path else None

        # Optional: a SameLevelHeadingsExtractor instance. If provided,  
        # adjust_chapters() will run a vision-model sanity check per  
        # cropped chapter PDF, asking NuExtract to independently identify  
        # which other headings in that chapter share its top-level  
        # heading's level. Purely additive/diagnostic -- does not feed  
        # back into our own level assignment.  
        self.same_level_extractor = same_level_extractor

        self._docling_headings = map_headings(self.docling_pages_dir)  
        self._page_paths = dict(iter_docling_page_files(self.docling_pages_dir))

    # ------------------------------------------------------------------ #  
    # Weak heading detection / materialization  
    # ------------------------------------------------------------------ #  
    @classmethod  
    def _extract_standalone_bold(cls, text):  
        m = cls.BOLD_ONLY_RE.match(text or "")  
        return m.group(1).strip() if m else None

    def _is_weak_heading(self, block):  
        return (  
            block.get("type") == self.PARAGRAPH_TYPE  
            and bool(self._extract_standalone_bold(block.get("content", "")))  
        )

    def _materialize_weak_heading(self, kid, assigned_level):  
        bold_text = self._extract_standalone_bold(kid.get("content", ""))

        kid["type"] = self.HEADING_TYPE  
        kid["level"] = assigned_level  
        kid["converted_from"] = self.PARAGRAPH_TYPE  
        kid["content"] = bold_text

    def _normalize_weak_headings(self, sections):  
        last_level = None  
        last_was_weak = False

        for section in sections:  
            for kid in section.get("kids", []):  
                if kid.get("type") == self.HEADING_TYPE:  
                    try:  
                        last_level = int(kid.get("level"))  
                    except (TypeError, ValueError):  
                        last_level = None  
                    last_was_weak = False  
                    continue

                if self._is_weak_heading(kid):  
                    if last_level is None:  
                        assigned_level = 1  
                    elif last_was_weak:  
                        assigned_level = last_level  
                    else:  
                        assigned_level = last_level - 1

                    self._materialize_weak_heading(kid, assigned_level)

                    last_level = assigned_level  
                    last_was_weak = True

    # ------------------------------------------------------------------ #  
    # Heading text normalization for Docling matching  
    # ------------------------------------------------------------------ #  
    @classmethod  
    def _heading_match_text(cls, content):  
        """  
        Returns `content` with any wrapping markdown emphasis markers  
        stripped from its very start/end, for matching against Docling  
        node text. Interior formatting/punctuation is left untouched. The  
        original `content` field stored on the heading block is NOT  
        modified -- this is only used as the lookup key for _find_bbox /  
        _find_earliest_bbox.  
        """  
        return cls.MD_EMPHASIS_RE.sub("", content or "").strip()

    # ------------------------------------------------------------------ #  
    # Heading collection / sanity check  
    # ------------------------------------------------------------------ #  
    @staticmethod  
    def _contains_heading_word(normalized_text, targets):  
        words = normalized_text.split()  
        return any(w in targets for w in words[:3])

    def _collect_heading_kids(self, chapter_json, chapter_name):  
        sections = chapter_json.get("document", {}).get("sections", [])  
        if not sections:  
            raise ValueError(f"No sections found in chapter file for '{chapter_name}'")

        self._normalize_weak_headings(sections)

        heading_kids = [  
            kid  
            for section in sections  
            for kid in section.get("kids", [])  
            if kid.get("type") == self.HEADING_TYPE  
        ]  
        if not heading_kids:  
            raise ValueError(f"No heading-type blocks found in chapter file for '{chapter_name}'")

        targets = set(normalize(chapter_name).split())  
        for section in sections:  
            targets.update(normalize(section.get("heading", "")).split())

        first = heading_kids[0]  
        first_text = self._heading_match_text(first.get("content", ""))  
        if not self._contains_heading_word(normalize(first_text), targets):  
            _log.warning(  
                "First heading block '%s' did not match expected keywords %s "  
                "for chapter '%s' -- file may be corrupted or misordered",  
                first.get("content"), targets, chapter_name,  
            )

        return heading_kids

    # ------------------------------------------------------------------ #  
    # Bbox resolution (start)  
    # ------------------------------------------------------------------ #  
    def _find_bbox(self, heading_text, page_no):  
        target = normalize(heading_text)

        same_page_match = None  
        any_match = None

        for heading in self._docling_headings:  
            if normalize(heading.get("text", "")) != target:  
                continue  
            if any_match is None:  
                any_match = heading  
            if heading.get("page") == page_no:  
                same_page_match = heading  
                break

        match = same_page_match or any_match  
        return match.get("bbox") if match else None

    def _find_earliest_bbox(self, heading_text):  
        """  
        Returns the (bbox, page) of the EARLIEST occurrence of  
        `heading_text` (normalized) anywhere in the document, by true  
        document order (page, then top-to-bottom) -- ignoring any  
        particular page preference.

        Used specifically when peeking at a different/next chapter file's  
        first heading to establish a section_end boundary, where the  
        page number that file itself reports for that heading cannot be  
        fully trusted (e.g. running-header duplicates of the same section  
        name appearing on later pages).

        Returns (None, None) if no match is found anywhere.  
        """  
        target = normalize(heading_text)  
        for heading in self._docling_headings:  
            if normalize(heading.get("text", "")) == target:  
                return heading.get("bbox"), heading.get("page")  
        return None, None

    # ------------------------------------------------------------------ #  
    # End-of-page fallback  
    # ------------------------------------------------------------------ #  
    def _page_bottom_bbox(self, page_no):  
        """  
        Returns a synthetic bbox representing the very bottom of the given  
        page, used as a section's end boundary when there's no following  
        chapter file at all to anchor against.

        Docling bboxes use a BOTTOMLEFT origin (y=0 is the bottom of the  
        page), so the bottom of the page is always t=0 regardless of  
        actual page height -- no page-dimension lookup is required for  
        that part. We opportunistically try to read the page's width so  
        "r" can be populated too, but fall back to None if the page  
        dimension isn't present in a recognized shape.  
        """  
        width = None  
        path = self._page_paths.get(page_no)  
        if path is not None:  
            try:  
                docling_page = load_json(path)  
            except Exception:  
                docling_page = {}

            for key in ("dimension", "size"):  
                dim = docling_page.get(key)  
                if isinstance(dim, dict) and "width" in dim:  
                    width = dim["width"]  
                    break

        return {"l": 0, "t": 0, "r": width, "b": 0}

    # ------------------------------------------------------------------ #  
    # Chapter file ordering on disk  
    # ------------------------------------------------------------------ #  
    def _chapter_sort_key(self, path):  
        m = self.LEADING_NUM_RE.match(path.stem)  
        if m:  
            return (int(m.group(1)), path.stem)  
        return (float("inf"), path.stem)

    def _all_chapter_files(self):  
        return sorted(self.chapters_dir.glob("*.json"), key=self._chapter_sort_key)

    # ------------------------------------------------------------------ #  
    # Peek at a chapter without adjusting/saving it  
    # ------------------------------------------------------------------ #  
    def _peek_first_heading(self, chapter_path):  
        """  
        Loads a chapter file purely to find its first heading-type block  
        (after normalizing any weak headings) and resolve that heading's  
        bbox. Used to find a section-end boundary for the last heading of  
        a chapter, when the next chapter in the real on-disk document  
        order wasn't itself requested for adjustment.

        Resolves the bbox/page via the EARLIEST matching occurrence of the  
        heading's text anywhere in the document (see _find_earliest_bbox),  
        rather than trusting this file's own self-reported page number --  
        that page number is not reliable enough on its own to disambiguate  
        against duplicate/running-header occurrences of the same text  
        later in the document.

        Read-only: does not mutate or save the file.  
        """  
        try:  
            chapter_json = load_json(chapter_path)  
        except Exception:  
            _log.warning("Could not load next chapter file %s to resolve end boundary", chapter_path)  
            return None

        sections = chapter_json.get("document", {}).get("sections", [])  
        if not sections:  
            return None

        self._normalize_weak_headings(sections)

        for section in sections:  
            for kid in section.get("kids", []):  
                if kid.get("type") == self.HEADING_TYPE:  
                    heading_text = self._heading_match_text(kid.get("content", ""))  
                    bbox, matched_page = self._find_earliest_bbox(heading_text)

                    if bbox is None:  
                        # No match anywhere in the document -- fall back to  
                        # the kid's own reported page, same-page-preferring  
                        # lookup, so we still return SOMETHING rather than  
                        # silently dropping the boundary.  
                        page_no = kid.get("page")  
                        bbox = self._find_bbox(heading_text, page_no)  
                        matched_page = page_no if bbox is not None else None

                    return {"page": matched_page, "bbox": bbox}

        return None

    # ------------------------------------------------------------------ #  
    # Shared per-chapter resolution (start bbox only)  
    # ------------------------------------------------------------------ #  
    def _resolve_chapter(self, chapter_name):  
        """  
        Loads a chapter file, normalizes weak headings, and resolves each  
        heading block's start bbox. Does NOT save and does NOT assign  
        section_end_bbox -- that's handled separately in adjust_chapters().

        Returns:  
            (chapter_path, chapter_json, heading_kids)  
        """  
        chapter_path = find_file_by_keyword(self.chapters_dir, chapter_name)  
        if chapter_path is None:  
            raise FileNotFoundError(f"No chapter file matching '{chapter_name}' found in {self.chapters_dir}")

        chapter_json = load_json(chapter_path)  
        heading_kids = self._collect_heading_kids(chapter_json, chapter_name)

        for kid in heading_kids:  
            page_no = kid.get("page")  
            heading_text = self._heading_match_text(kid.get("content", ""))  
            bbox = self._find_bbox(heading_text, page_no)

            if bbox is None:  
                _log.warning(  
                    "Could not find a docling heading matching '%s' (page %s, file: %s); "  
                    "setting bbox to None",  
                    kid.get("content"), page_no, chapter_path.name,  
                )

            kid["bbox"] = bbox

        return chapter_path, chapter_json, heading_kids

    # ------------------------------------------------------------------ #  
    def _save(self, chapter_path, chapter_json):  
        outfile = (self.outputs_path / chapter_path.name) if self.outputs_path else chapter_path  
        save_json(chapter_json, outfile)  
        _log.info("Saved heading-adjusted chapter file to %s", outfile)  
        return outfile

    # ------------------------------------------------------------------ #  
    def adjust(self, chapter_name):  
        """  
        Resolves start bboxes for a single chapter and saves it. Does NOT  
        populate section_end_bbox, since that requires knowing what comes  
        after this chapter on disk -- use adjust_chapters() for that.  
        """  
        chapter_path, chapter_json, _ = self._resolve_chapter(chapter_name)  
        self._save(chapter_path, chapter_json)  
        return chapter_json

    # ------------------------------------------------------------------ #  
    def _assign_section_ends(self, chapter_name, info, file_index_by_name, all_files):  
        """  
        Assigns "section_end_bbox" / "section_end_page" to every heading in  
        a single chapter's heading_kids list:

            - for all but the last heading, the end boundary is simply the  
              START of the next heading in this SAME chapter's own  
              (already correctly ordered) list.  
            - for the last heading, the end boundary is the START of the  
              first heading in the NEXT chapter file on disk (by true file  
              order), regardless of whether that next chapter was itself  
              requested/processed. If there is no next chapter file at  
              all, falls back to the bottom of that heading's own page.  
        """  
        heading_kids = info["heading_kids"]  
        chapter_path = info["path"]

        for i in range(len(heading_kids) - 1):  
            nxt = heading_kids[i + 1]  
            heading_kids[i]["section_end_bbox"] = nxt.get("bbox")  
            heading_kids[i]["section_end_page"] = nxt.get("page")

        last_kid = heading_kids[-1]  
        this_idx = file_index_by_name.get(chapter_path.name)

        next_heading = None  
        if this_idx is not None and this_idx + 1 < len(all_files):  
            next_chapter_path = all_files[this_idx + 1]  
            next_heading = self._peek_first_heading(next_chapter_path)  
            if next_heading is None:  
                _log.warning(  
                    "Next chapter file %s has no heading blocks; falling back "  
                    "to end-of-page for the final heading of '%s'",  
                    next_chapter_path.name, chapter_name,  
                )

        if next_heading is not None:  
            last_kid["section_end_bbox"] = next_heading.get("bbox")  
            last_kid["section_end_page"] = next_heading.get("page")  
        else:  
            page_no = last_kid.get("page")  
            last_kid["section_end_bbox"] = self._page_bottom_bbox(page_no)  
            last_kid["section_end_page"] = page_no  
            _log.info(  
                "Heading '%s' is the last heading in the document (no further "  
                "chapter files found on disk); using bottom of page %s as its "  
                "section_end_bbox",  
                last_kid.get("content"), page_no,  
            )

    # ------------------------------------------------------------------ #  
    def adjust_chapters(self, chapter_names):  
        """  
        Resolves start bboxes for each chapter in `chapter_names` that  
        actually exists on disk (missing ones are skipped with a warning),  
        then assigns each heading a "section_end_bbox" / "section_end_page":  
        the start of the next heading in the SAME chapter for all but a  
        chapter's last heading, and the start of the NEXT CHAPTER FILE's  
        first heading (true on-disk order, regardless of whether that  
        chapter was itself requested) for each chapter's final heading.

        If `self.pdf_path` was provided, also crops and saves a separate  
        PDF per processed chapter, spanning from that chapter's first  
        heading to its last heading's section_end boundary, saved  
        alongside the adjusted JSON with the same filename stem.

        Saves all successfully processed chapters and returns them as a  
        dict keyed by chapter_name.  
        """  
        all_files = self._all_chapter_files()  
        if not all_files:  
            raise FileNotFoundError(f"No chapter files found in {self.chapters_dir}")

        file_index_by_name = {path.name: i for i, path in enumerate(all_files)}

        resolved = {}

        for chapter_name in chapter_names:  
            try:  
                chapter_path, chapter_json, heading_kids = self._resolve_chapter(chapter_name)  
            except FileNotFoundError:  
                _log.warning(  
                    "No chapter file matching '%s' found in %s; skipping",  
                    chapter_name, self.chapters_dir,  
                )  
                continue

            resolved[chapter_name] = {  
                "path": chapter_path,  
                "json": chapter_json,  
                "heading_kids": heading_kids,  
            }

        if not resolved:  
            _log.warning(  
                "None of the requested chapters %s were found in %s",  
                chapter_names, self.chapters_dir,  
            )  
            return {}

        for chapter_name, info in resolved.items():  
            self._assign_section_ends(chapter_name, info, file_index_by_name, all_files)

        output_jsons = {}  
        for chapter_name, info in resolved.items():  
            chapter_path = info["path"]  
            chapter_json = info["json"]

            outfile = self._save(chapter_path, chapter_json)  
            output_jsons[chapter_name] = chapter_json

            if self.pdf_path is not None:  
                pdf_outfile = outfile.with_suffix(".pdf")  
                try:  
                    crop_chapter_pdf(self.pdf_path, chapter_json, pdf_outfile)  
                except Exception:  
                    _log.exception(  
                        "Failed to crop chapter PDF for '%s' (file: %s)",  
                        chapter_name, chapter_path.name,  
                    )  
                else:  
                    if self.same_level_extractor is not None:  
                        heading_kids = info["heading_kids"]

                        # heading_kids[0] is the chapter's own title heading  
                        # (e.g. "Methods") -- nothing else in the cropped  
                        # PDF shares ITS level. The useful anchor is the  
                        # first heading one level deeper (e.g. "Patient  
                        # Sample"), since that's the one with real siblings  
                        # to find.  
                        if len(heading_kids) < 2:  
                            _log.warning(  
                                "Chapter '%s' has no sub-headings below its own "  
                                "title heading; skipping same-level heading "  
                                "extraction (file: %s)",  
                                chapter_name, chapter_path.name,  
                            )  
                        else:  
                            anchor_heading = heading_kids[1].get("content", "")  
                            try:  
                                self.same_level_extractor.extract(pdf_outfile, anchor_heading)  
                            except Exception:  
                                _log.exception(  
                                    "Failed to extract same-level headings for '%s' (file: %s)",  
                                    chapter_name, pdf_outfile.name,  
                                )

        return output_jsons