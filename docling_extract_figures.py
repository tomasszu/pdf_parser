import logging  
import re  
import time  
from pathlib import Path  
from typing import Optional, Tuple
import fitz  # PyMuPDF
from PIL import Image

from docling_core.types.doc import ImageRefMode, PictureItem, TableItem, TextItem  
from docling.datamodel.base_models import InputFormat  
from docling.datamodel.pipeline_options import PdfPipelineOptions  
from docling.document_converter import DocumentConverter, PdfFormatOption


_log = logging.getLogger(__name__)


class DoclingPdfPageProcessor:  
    def __init__(  
        self,  
        output_dir: str | Path,  
        image_resolution_scale: float = 2.0,  
        generate_page_images: bool = True,
        generate_picture_images: bool = True,  
        logger: Optional[logging.Logger] = None 
    ):  
        self.output_dir = Path(output_dir).resolve()  
        self.image_resolution_scale = image_resolution_scale  
        self.generate_page_images = generate_page_images  
        self.generate_picture_images = generate_picture_images  
        self.logger = logger or _log
        self.doc_converter = self._build_converter()

    def _build_converter(self) -> DocumentConverter:  
        pipeline_options = PdfPipelineOptions()  
        pipeline_options.images_scale = self.image_resolution_scale  
        pipeline_options.generate_page_images = self.generate_page_images  
        pipeline_options.generate_picture_images = self.generate_picture_images

        return DocumentConverter(  
            format_options={  
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }  
        )

    def process_pdf(  
        self,  
        input_pdf_path: str | Path,  
        start_page: Optional[int] = None,  
        end_page: Optional[int] = None,  
        save_json_per_page: bool = False,  
    ) -> None:  
        """  
        Process a PDF one page at a time from start_page to end_page inclusive.  
        """

        input_pdf_path = Path(input_pdf_path).resolve()  
        self.output_dir.mkdir(parents=True, exist_ok=True)

        overall_start = time.time()

        total_pages = self.get_pdf_page_count(input_pdf_path)

        if start_page is None:  
            start_page = 1  
        if end_page is None:  
            end_page = total_pages

        if start_page < 1:  
                    raise ValueError(f"start_page must be >= 1, got {start_page}")  
        if end_page > total_pages:  
            raise ValueError(  
                f"end_page ({end_page}) exceeds total pages in PDF ({total_pages})"  
            )  
        if start_page > end_page:  
            raise ValueError(  
                f"start_page ({start_page}) cannot be greater than end_page ({end_page})"  
            )

        self.logger.info(  
            f"Processing pages {start_page} to {end_page} of {total_pages} total pages."  
        )

        for page_no in range(start_page, end_page + 1):  
            self.logger.info(f"Processing page {page_no}...")  
            try:  
                self.process_single_page(  
                    input_pdf_path=input_pdf_path,  
                    page_no=page_no,  
                    save_json=save_json_per_page,  
                )
            except Exception as e:  
                self.logger.exception(f"Failed to process page {page_no}: {e}")

        elapsed = time.time() - overall_start  
        self.logger.info(f"Finished processing PDF in {elapsed:.2f} seconds.")

    def process_single_page(
        self,  
        input_pdf_path: str | Path,  
        page_no: int,  
        save_json: bool = False,
    ) -> None:  
        """  
        Convert and process exactly one page.  
        """

        self.logger.info(f"\n=== Docling processing page {page_no} ===\n")
        input_pdf_path = Path(input_pdf_path).resolve()

        start_time = time.time()

        conv_res = self.doc_converter.convert(  
            str(input_pdf_path),  
            page_range=(page_no, page_no),  
        )

        doc_filename = conv_res.input.file.stem

        table_counter = 0  
        picture_counter = 0  
        prev_element = None

        picture_candidates = []

        for element, _level in conv_res.document.iterate_items():  
            if isinstance(element, TableItem):  
                table_counter += 1  
                self._save_table_image(  
                    element=element,  
                    document=conv_res.document,  
                    page_no=page_no,  
                    table_counter=table_counter,  
                    prev_element=prev_element,  
                )

            elif isinstance(element, PictureItem):  
                picture_counter += 1  
                candidate = self._collect_picture_candidate(  
                    element=element,  
                    document=conv_res.document,  
                    page_no=page_no,  
                    picture_counter=picture_counter,  
                )  
                if candidate is not None:  
                    picture_candidates.append(candidate)

            prev_element = element

        self._finalize_page_pictures(  
            picture_candidates=picture_candidates,  
            document=conv_res.document,  
            page_no=page_no,  
        )

        if save_json:  
            json_path = self.output_dir / str(page_no) / f"{doc_filename}-page-{page_no}.json"  
            json_path.parent.mkdir(parents=True, exist_ok=True)  
            conv_res.document.save_as_json(json_path, image_mode=ImageRefMode.REFERENCED)

        elapsed = time.time() - start_time  
        self.logger.info(f"Page {page_no} processed in {elapsed:.2f} seconds.")  

    def _save_table_image(  
        self,  
        element: TableItem,  
        document,  
        page_no: int,  
        table_counter: int,  
        prev_element,  
    ) -> None:  
        cap_nr = self._extract_table_caption_number(element, document, prev_element)

        if cap_nr is not None:  
            image_path = self.output_dir / str(page_no) / "tables" / f"{cap_nr}.png"  
        else:  
            image_path = self.output_dir / str(page_no) / "tables" / f"no_cap_{table_counter}.png"

        image_path.parent.mkdir(parents=True, exist_ok=True)  
        with image_path.open("wb") as fp:  
            element.get_image(document).save(fp, "PNG")

    def _save_picture_image(  
        self,  
        element: PictureItem,  
        document,  
        page_no: int,  
        picture_counter: int,  
    ) -> None:  
        cap_nr = self._extract_picture_caption_number(element, document)

        if cap_nr is not None:  
            image_path = self.output_dir / str(page_no) / "images" / f"{cap_nr}.png"  
        else:  
            image_path = self.output_dir / str(page_no) / "images" / f"no_cap_{picture_counter}.png"

        image = element.get_image(document)
        if image is None:
            self.logger.warning(  
                f"Skipping picture on page {page_no}: no image could be generated." 
            )  
            return
        
        w, h = image.size  
        if w < 150 or h < 150:  
            self.logger.warning(  
                f"Skipping picture on page {page_no}: image too small ({w}x{h})."  
            )  
            return

        image_path.parent.mkdir(parents=True, exist_ok=True)  
        with image_path.open("wb") as fp:  
            image.save(fp, "PNG")

    def _collect_picture_candidate(  
        self,  
        element: PictureItem,  
        document,  
        page_no: int,  
        picture_counter: int,  
    ) -> dict | None:  
        image = element.get_image(document)  
        if image is None:  
            self.logger.warning(  
                f"Skipping picture on page {page_no}: no image could be generated."  
            )  
            return None

        w, h = image.size  
        cap_data = self._extract_picture_caption_data(element, document)

        bbox = None  
        prov = getattr(element, "prov", None) or []  
        if prov:  
            bbox = getattr(prov[0], "bbox", None)

        saveable = not (w < 150 or h < 150)  
        skip_reason = None  
        if not saveable:  
            skip_reason = f"too_small ({w}x{h})"  
            self.logger.warning(  
                f"Picture on page {page_no} marked non-saveable: image too small ({w}x{h})."  
            )

        return {  
            "element": element,  
            "image": image,  
            "page_no": page_no,  
            "picture_counter": picture_counter,  
            "self_ref": getattr(element, "self_ref", None),  
            "bbox": bbox,  
            "width": w,  
            "height": h,  
            "caption_number": cap_data["number"],  
            "caption_text": cap_data["caption"],  
            "caption_source": cap_data["source"],  
            "resolved": cap_data["number"] is not None,  
            "saveable": saveable,  
            "skip_reason": skip_reason,  
            "used_in_merge": False,  
            "used_caption_ref": None,  
        }  

    def _extract_picture_caption_data(self, element: PictureItem, document) -> dict:  
        """  
        Try multiple strategies to find the figure caption data:
        1. linked caption  
        2. child text elements

        Returns:  
            {  
                "number": int | None,  
                "caption": str | None,  
                "source": str | None,   # linked_caption | child_text | None  
            }  
        """  
        cap = element.caption_text(document)

        # 1. linked caption  
        if cap:  
            cap_text = self._strip_md(cap)  
            cap_data = self._extract_opendata_figure_number(cap_text)  
            if cap_data["number"] is not None:  
                return {  
                    "number": cap_data["number"],  
                    "caption": cap_data["caption"],  
                    "source": "linked_caption",  
                }

        # 2. search in children elements  
        for child in getattr(element, "children", []):  
            ref = getattr(child, "cref", None)  
            if ref is None:  
                ref = getattr(child, "$ref", None) if hasattr(child, "$ref") else None  
            if ref is None and isinstance(child, dict):  
                ref = child.get("$ref") or child.get("cref")  
            if ref is None:  
                continue

            for el in getattr(document, "texts", []):  
                if getattr(el, "self_ref", None) == ref:  
                    text = getattr(el, "text", "")  
                    cap_data = self._extract_opendata_figure_number(text)  
                    if cap_data["number"] is not None:  
                        return {  
                            "number": cap_data["number"],  
                            "caption": cap_data["caption"],  
                            "source": "child_text",  
                        }

        return {  
            "number": None,  
            "caption": None,  
            "source": None,  
        }


    def _finalize_page_pictures(self, picture_candidates: list[dict], document, page_no: int) -> None:  
        saveable_candidates = [p for p in picture_candidates if p["saveable"]]  
        discarded_candidates = [p for p in picture_candidates if not p["saveable"]]

        resolved = [p for p in saveable_candidates if p["resolved"]]  
        unresolved = [p for p in saveable_candidates if not p["resolved"]]

        self.logger.info(  
            f"Page {page_no}: {len(saveable_candidates)} valid pictures, "  
            f"{len(resolved)} resolved, {len(unresolved)} unresolved."  
        )

        if discarded_candidates:  
            self.logger.info(  
                f"Page {page_no}: {len(discarded_candidates)} discarded pictures retained for reconciliation."  
            )

        text_candidates = self._collect_page_figure_text_candidates(document, page_no)  
        self._mark_used_text_captions(text_candidates, saveable_candidates)

        merged_resolved_refs = set()  
        merged_unresolved_refs = set()

        # 1. try merging unresolved with resolved  
        for unres in unresolved:  
            for res in resolved:  
                orientation = self._are_likely_split_figures(unres, res)  
                if orientation is None:  
                    continue

                fig_nr = res["caption_number"]  
                if fig_nr is None:  
                    continue

                merged = self._stitch_images(unres, res, orientation)  
                self._save_stitched_picture(merged, page_no=page_no, figure_number=fig_nr)

                merged_resolved_refs.add(res["self_ref"])  
                merged_unresolved_refs.add(unres["self_ref"])

                self.logger.info(  
                    f"Page {page_no}: merged split figure {unres['self_ref']} + {res['self_ref']} "  
                    f"into figure {fig_nr} ({orientation}). caption={res['caption_text']!r}"  
                )  
                break

        # 2. assign nearby unused text captions to unresolved leftovers  
        for unres in unresolved:  
            if unres["self_ref"] in merged_unresolved_refs:  
                continue

            text_match = self._find_nearby_unused_caption_for_picture(unres, text_candidates)  
            if text_match is not None:  
                unres["caption_number"] = text_match["number"]  
                unres["caption_text"] = text_match["text"]  
                unres["caption_source"] = "nearby_page_text"  
                unres["resolved"] = True  
                text_match["used"] = True

            self.logger.info(  
                f"Page {page_no}: assigned nearby text caption {text_match['self_ref']} "  
                f"to unresolved picture {unres['self_ref']} as figure {text_match['number']} "  
                f"(placement={text_match.get('placement')}, score={text_match.get('score'):.2f})."  
            )

        # 3. recover caption from discarded tiny picture if possible  
        for unres in unresolved:  
            if unres["self_ref"] in merged_unresolved_refs:  
                continue  
            if unres["resolved"]:  
                continue

            donor = self._find_caption_donor_from_discarded_picture(unres, discarded_candidates)  
            if donor is not None:  
                unres["caption_number"] = donor["caption_number"]  
                unres["caption_text"] = donor["caption_text"]  
                unres["caption_source"] = "discarded_picture_donor"  
                unres["resolved"] = True

                self.logger.info(  
                    f"Page {page_no}: reassigned caption from discarded picture {donor['self_ref']} "  
                    f"to unresolved picture {unres['self_ref']} as figure {donor['caption_number']}."  
                )

        # 4. save resolved singles that were not merged  
        for res in resolved:  
            if res["self_ref"] in merged_resolved_refs:  
                continue  
            self._save_picture_image_from_candidate(res, final_number=res["caption_number"])  
            self.logger.info(  
                f"Page {page_no}: saved resolved picture {res['self_ref']} "  
                f"as figure {res['caption_number']}."  
            )

        # also save newly resolved former-unresolved  
        for unres in unresolved:  
            if unres["self_ref"] in merged_unresolved_refs:  
                continue  
            if unres["resolved"]:  
                self._save_picture_image_from_candidate(unres, final_number=unres["caption_number"])  
                self.logger.info(  
                    f"Page {page_no}: saved reconciled picture {unres['self_ref']} "  
                    f"as figure {unres['caption_number']} ({unres['caption_source']})."  
                )

        # 5. save unresolved leftovers  
        for unres in unresolved:  
            if unres["self_ref"] in merged_unresolved_refs:  
                continue  
            if unres["resolved"]:  
                continue  
            self._save_picture_image_from_candidate(unres, final_number=None)  
            self.logger.info(  
                f"Page {page_no}: unresolved picture {unres['self_ref']} "  
                f"saved as no_cap_{unres['picture_counter']}."  
            )  



    def _save_picture_image_from_candidate(self, candidate: dict, final_number: int | None = None) -> None:  
        page_no = candidate["page_no"]  
        picture_counter = candidate["picture_counter"]  
        image = candidate["image"]

        if final_number is not None:  
            image_path = self.output_dir / str(page_no) / "images" / f"{final_number}.png"  
        else:  
            image_path = self.output_dir / str(page_no) / "images" / f"no_cap_{picture_counter}.png"

        image_path.parent.mkdir(parents=True, exist_ok=True)  
        with image_path.open("wb") as fp:  
            image.save(fp, "PNG")

    def _collect_page_figure_text_candidates(self, document, page_no: int) -> list[dict]:
        candidates = []

        for el in getattr(document, "texts", []):  
            text = getattr(el, "text", "") or ""  
            clean_text = self._strip_md(text)

            cap_data = self._extract_opendata_figure_number(clean_text)  
            if not cap_data or cap_data["number"] is None:  
                continue

            prov = getattr(el, "prov", None) or []  
            bbox = getattr(prov[0], "bbox", None) if prov else None

            # if prov exists and page filtering matters  
            if prov:  
                prov_page_no = getattr(prov[0], "page_no", None)  
                if prov_page_no is not None and prov_page_no != page_no:  
                    continue

            candidates.append({  
                "self_ref": getattr(el, "self_ref", None),  
                "text": clean_text,  
                "number": cap_data["number"],  
                "bbox": bbox,  
                "used": False,  
            })

        return candidates

    def _mark_used_text_captions(self, text_candidates: list[dict], picture_candidates: list[dict]) -> None:  
        for pic in picture_candidates:  
            if pic["caption_number"] is None:  
                continue

            for text_cand in text_candidates:  
                if text_cand["used"]:  
                    continue

                same_number = text_cand["number"] == pic["caption_number"]  
                same_text = (  
                    pic["caption_text"] is not None and  
                    text_cand["text"].strip() == pic["caption_text"].strip()  
                )

                if same_number and same_text:  
                    text_cand["used"] = True  
                    pic["used_caption_ref"] = text_cand["self_ref"]  
                    break

    def _vertical_gap_image_to_caption(self, image_bbox, caption_bbox) -> float | None:  
        if image_bbox is None or caption_bbox is None:  
            return None

        # caption below image in BOTTOMLEFT coordinates:  
        # image bottom is b, caption top is t  
        return abs(image_bbox.b - caption_bbox.t)

    def _horizontal_center_distance(self, bbox1, bbox2) -> float | None:  
        if bbox1 is None or bbox2 is None:  
            return None

        c1 = (bbox1.l + bbox1.r) / 2  
        c2 = (bbox2.l + bbox2.r) / 2  
        return abs(c1 - c2)

    def _find_nearby_unused_caption_for_picture(
        self,  
        picture: dict,  
        text_candidates: list[dict],  
    ) -> dict | None:  
        pic_bbox = picture.get("bbox")  
        if pic_bbox is None:  
            return None

        best = None  
        best_score = None

        for text_cand in text_candidates:  
            if text_cand["used"]:  
                continue  
            if text_cand["bbox"] is None:  
                continue

            text_bbox = text_cand["bbox"]

            horizontal_overlap = self._horizontal_overlap_ratio(pic_bbox, text_bbox)  
            vertical_overlap = self._vertical_overlap_ratio(pic_bbox, text_bbox)

            below_gap = self._vertical_gap_if_below(pic_bbox, text_bbox)  
            left_gap = self._horizontal_gap_if_left(pic_bbox, text_bbox)  
            right_gap = self._horizontal_gap_if_right(pic_bbox, text_bbox)

            center_dist = self._bbox_center_distance(pic_bbox, text_bbox)

            placement = None  
            score = None

            # 1. caption below image  
            if below_gap is not None and horizontal_overlap >= 0.4:  
                # below is the most common case, so allow a slightly better preference  
                score_below = below_gap + 0.15 * center_dist  
                placement = "below"  
                score = score_below

            # 2. caption on the left side  
            if left_gap is not None and vertical_overlap >= 0.4:  
                # side captions may be farther away, so tolerate larger gap  
                score_left = left_gap + 0.25 * center_dist + 10  
                if score is None or score_left < score:  
                    placement = "left"  
                    score = score_left

            # 3. caption on the right side  
            if right_gap is not None and vertical_overlap >= 0.4:  
                score_right = right_gap + 0.25 * center_dist + 10  
                if score is None or score_right < score:  
                    placement = "right"  
                    score = score_right

            if placement is None:  
                continue

            # hard upper bounds to avoid absurd matches  
            if placement == "below" and below_gap > 120:  
                continue  
            if placement in {"left", "right"}:  
                side_gap = left_gap if placement == "left" else right_gap  
                if side_gap > 220:  
                    continue

            if best_score is None or score < best_score:  
                best = text_cand.copy()  
                best["placement"] = placement  
                best["score"] = score  
                best_score = score

        return best

    def _vertical_gap_if_below(self, image_bbox, text_bbox) -> float | None:  
        if image_bbox is None or text_bbox is None:  
            return None

        # text below image in BOTTOMLEFT coordinates:  
        # text top should be at or below image bottom (with a bit of tolerance)  
        if text_bbox.t > image_bbox.b + 20:  
            return None

        return max(0.0, image_bbox.b - text_bbox.t)

    def _horizontal_gap_if_left(self, image_bbox, text_bbox) -> float | None:  
        if image_bbox is None or text_bbox is None:  
            return None

        # text must be left of image  
        if text_bbox.r > image_bbox.l + 20:  
            return None

        return max(0.0, image_bbox.l - text_bbox.r)

    def _horizontal_gap_if_right(self, image_bbox, text_bbox) -> float | None:  
        if image_bbox is None or text_bbox is None:  
            return None

        # text must be right of image  
        if text_bbox.l < image_bbox.r - 20:  
            return None

        return max(0.0, text_bbox.l - image_bbox.r)

    def _bbox_center_distance(self, bbox1, bbox2) -> float:  
        c1x = (bbox1.l + bbox1.r) / 2  
        c1y = (bbox1.t + bbox1.b) / 2  
        c2x = (bbox2.l + bbox2.r) / 2  
        c2y = (bbox2.t + bbox2.b) / 2  
        return ((c1x - c2x) ** 2 + (c1y - c2y) ** 2) ** 0.5

    def _find_caption_donor_from_discarded_picture(
        self,  
        picture: dict,  
        discarded_candidates: list[dict],  
    ) -> dict | None:  
        pic_bbox = picture.get("bbox")  
        if pic_bbox is None:  
            return None

        best = None  
        best_score = None

        for donor in discarded_candidates:  
            if donor["caption_number"] is None:  
                continue  
            donor_bbox = donor.get("bbox")  
            if donor_bbox is None:  
                continue

            horizontal_overlap = self._horizontal_overlap_ratio(pic_bbox, donor_bbox)  
            vertical_overlap = self._vertical_overlap_ratio(pic_bbox, donor_bbox)

            gap = min(  
                abs(pic_bbox.b - donor_bbox.t),  
                abs(donor_bbox.b - pic_bbox.t),  
                abs(pic_bbox.r - donor_bbox.l),  
                abs(donor_bbox.r - pic_bbox.l),  
            )

            # donor is tiny, but near the actual figure/caption region  
            if horizontal_overlap < 0.3 and vertical_overlap < 0.3 and gap > 120:  
                continue

            score = gap  
            if best_score is None or score < best_score:  
                best = donor  
                best_score = score

        return best

    def _are_likely_split_figures(self, a: dict, b: dict) -> str | None:
        """  
        Returns:  
            "horizontal" if side-by-side merge  
            "vertical" if stacked merge  
            None otherwise  
        """  
        if not a.get("bbox") or not b.get("bbox"):  
            return None

        bbox_a = a["bbox"]  
        bbox_b = b["bbox"]

        l1, r1, t1, b1, w1, h1 = self._bbox_metrics(bbox_a)  
        l2, r2, t2, b2, w2, h2 = self._bbox_metrics(bbox_b)

        vertical_overlap = self._vertical_overlap_ratio(bbox_a, bbox_b)  
        horizontal_overlap = self._horizontal_overlap_ratio(bbox_a, bbox_b)

        # similar heights for side-by-side panels  
        height_ratio = min(h1, h2) / max(h1, h2) if max(h1, h2) > 0 else 0  
        # similar widths for stacked panels  
        width_ratio = min(w1, w2) / max(w1, w2) if max(w1, w2) > 0 else 0

        horizontal_gap = min(abs(r1 - l2), abs(r2 - l1))  
        vertical_gap = min(abs(b1 - t2), abs(b2 - t1))

        # side-by-side  
        if vertical_overlap > 0.8 and height_ratio > 0.85 and horizontal_gap < 40:  
            return "horizontal"

        # stacked  
        if horizontal_overlap > 0.8 and width_ratio > 0.85 and vertical_gap < 40:  
            return "vertical"

        return None

    def _stitch_images(self, a: dict, b: dict, orientation: str):  
        img_a = a["image"]  
        img_b = b["image"]

        if orientation == "horizontal":  
            # preserve left-to-right order by bbox  
            if a["bbox"].l <= b["bbox"].l:  
                left_img, right_img = img_a, img_b  
            else:  
                left_img, right_img = img_b, img_a

            new_img = Image.new("RGB", (left_img.width + right_img.width, max(left_img.height, right_img.height)), "white")  
            new_img.paste(left_img, (0, 0))  
            new_img.paste(right_img, (left_img.width, 0))  
            return new_img

        if orientation == "vertical":  
            # preserve top-to-bottom order by bbox  
            if a["bbox"].t >= b["bbox"].t:  
                top_img, bottom_img = img_a, img_b  
            else:  
                top_img, bottom_img = img_b, img_a

            new_img = Image.new("RGB", (max(top_img.width, bottom_img.width), top_img.height + bottom_img.height), "white")  
            new_img.paste(top_img, (0, 0))  
            new_img.paste(bottom_img, (0, top_img.height))  
            return new_img

        raise ValueError(f"Unknown orientation: {orientation}")

    def _save_stitched_picture(self, merged_image, page_no: int, figure_number: int) -> None:  
        image_path = self.output_dir / str(page_no) / "images" / f"{figure_number}.png"  
        image_path.parent.mkdir(parents=True, exist_ok=True)  
        with image_path.open("wb") as fp:  
            merged_image.save(fp, "PNG")

    def _bbox_metrics(self, bbox):  
        left = bbox.l  
        right = bbox.r  
        top = bbox.t  
        bottom = bbox.b  
        width = right - left  
        height = top - bottom  
        return left, right, top, bottom, width, height

    def _vertical_overlap_ratio(self, bbox1, bbox2):  
        l1, r1, t1, b1, w1, h1 = self._bbox_metrics(bbox1)  
        l2, r2, t2, b2, w2, h2 = self._bbox_metrics(bbox2)

        overlap = max(0, min(t1, t2) - max(b1, b2))  
        denom = min(h1, h2)  
        return overlap / denom if denom > 0 else 0.0

    def _horizontal_overlap_ratio(self, bbox1, bbox2):  
        l1, r1, t1, b1, w1, h1 = self._bbox_metrics(bbox1)  
        l2, r2, t2, b2, w2, h2 = self._bbox_metrics(bbox2)

        overlap = max(0, min(r1, r2) - max(l1, l2))  
        denom = min(w1, w2)  
        return overlap / denom if denom > 0 else 0.0  

    def _extract_table_caption_number(self, element: TableItem, document, prev_element) -> Optional[int]:  
        """  
        Try multiple strategies to find the table number:  
        1. linked caption  
        2. table cell content  
        3. preceding text element  
        """  
        cap = element.caption_text(document)

        # 1. linked caption  
        if cap:  
            cap_text = self._strip_md(cap)  
            cap_nr = self._extract_opendata_table_number(cap_text)  
            if cap_nr is not None:  
                return cap_nr

        # 2. search in table cells  
        for cell in element.data.table_cells:  
            text = cell.text  
            cap_nr = self._extract_opendata_table_number(text)  
            if cap_nr is not None:  
                return cap_nr

        # 3. search in preceding element  
        return self._search_for_table_caption_nr(prev_element)

    def _search_for_table_caption_nr(self, preceding_element) -> Optional[int]:  
        if preceding_element and isinstance(preceding_element, TextItem):  
            text = preceding_element.text  
            return self._extract_opendata_table_number(text)  
        return None

    def get_pdf_page_count(self, file_path):
        # Open the document
        doc = fitz.open(file_path)
        
        # Get the page count
        return doc.page_count

    def _extract_opendata_table_number(self, text: str) -> Optional[int]:  
        text = self._strip_md(text)
        m = re.search(  
            r"^\s*table\s+(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return int(m.group(1)) if m else None

    def _extract_opendata_figure_number(self, text):  
        text = self._strip_md(text)  
        m = re.search(  
            r"^\s*(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return {  
            "number": int(m.group(1)) if m else None,  
            "caption": text.strip() if text else None,  
        }

    def _strip_md(self, text: str) -> str:  
        text = text.replace("**", "").replace("__", "")  
        text = text.replace("*", "").replace("_", "")  
        text = re.sub(r"\s+", " ", text)  
        return text.strip()