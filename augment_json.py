# This module defines the CleanupJSON class used to sanitize parsed PDF JSON outputs.

import json  
import re  
import shutil  
from pathlib import Path  
from typing import List, Dict, Any, Optional  
from collections import defaultdict

import fitz  # PyMuPDF


class AugmentJSON:  
    """Utility class to combine and augment the JSON representation of parsed PDFs from both NuExtract and Opendataloader parsers.

    Fixes the problem of parsed pdf json being imperfect.

    """

    def __init__(self, output_dir: Path, output_file: Path):  
        # output folder  
        self.output_dir = output_dir  
        self.output_file = output_file

        # cropped tables images folder  
        self.tables_dir = f"{output_dir}/tables"  
        self.tables_dir = Path(self.tables_dir)  
        self.tables_dir.mkdir(parents=True, exist_ok=True)

    def run(self, nuext_input_json: Path, opendata_input_json: Path, pdf_path: Path) -> None:  
        """Process, sort, crop tables, and clean up artifact paragraphs inside images, align images with captions."""

        # Load JSON data  
        with open(nuext_input_json, "r", encoding="utf-8") as f:  
            cleaned_entries = json.load(f)

        with open(opendata_input_json, "r", encoding="utf-8") as f:  
            opendata_data = json.load(f)

        # kids are the entries (paragraphs, images, tables, lists ...)  
        if "kids" not in opendata_data:  
            print("'kids' in JSON structure not found")  
            return

        # 1. Clean up artifact paragraphs that fall inside figures/images  
        opendata_data["kids"] = self._remove_contained_paragraphs(opendata_data["kids"])

        # 2. Save tables as images  
        # Open the PDF document once  
        with fitz.open(pdf_path) as pdf_doc:  
            # Iterate over entries looking for tables  
            for entry in opendata_data.get("kids", []):  
                if entry.get("type") == "table":  
                    self._process_table_entry(entry, pdf_doc)

        # Step 3. Align misplaced figures with their captions  
        opendata_data["kids"] = self._reorder_figures_and_captions(opendata_data["kids"])

        # Write back the modified JSON  
        with open(self.output_file, "w", encoding="utf-8") as f:  
            json.dump(opendata_data, f, indent=2, ensure_ascii=False)

        # <<<<<<<<<<<<<<<<<<<<<<<<The following deals with merging the images of figures and tables into the nuextract cleaned up json.>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

        output_folders_path = Path(nuext_input_json).parent  
        input_folders_path = Path(opendata_input_json).parent

        opendata_entries = opendata_data["kids"]

        figure_map = self._build_figure_map(opendata_entries)  
        table_map = self._build_table_map(opendata_entries)

        print("\n=== Finding Tables and Figures in the OpenDataParser json ===")

        print("\nFigures found:")  
        for k, v in figure_map.items():  
            print(f"  {k} -> {v}")

        print("\nTables found:")  
        for k, v in table_map.items():  
            print(f"  {k} -> {v}")

        print("\n=======================================================")  
        print("\n=== Finding Tables and Figures in the NuExtract json and matching them against Opendata json ===")

        out = []  
        for i, entry in enumerate(cleaned_entries):  
            e = dict(entry)

            if e.get("type") == "figure":  
                fig_num = self._extract_nuext_figure_number(cleaned_entries, i)

                if fig_num in figure_map:  
                    e["content"] = figure_map[fig_num]  
                    print(f"[OK] matched figure {fig_num}")  
                else:  
                    print(f"[MISS] figure {fig_num}")

            elif e.get("type") == "table":  
                table_num = self._extract_nuext_table_number(cleaned_entries, i)

                if table_num in table_map:  
                    e["content"] = table_map[table_num]  
                    print(f"[OK] matched table {table_num}")  
                else:  
                    print(f"[MISS] table {table_num}")
                    print(f"  missing content preview: {repr((e.get('content', '') or '')[:500])}")

            out.append(e)

        output_path = output_folders_path / "combined_blocks_augmented.json"  
        with open(output_path, "w", encoding="utf-8") as f:  
            json.dump(out, f, indent=2, ensure_ascii=False)

        self._copy_assets(input_folders_path, output_folders_path)

    def _copy_assets(self, inputs_dir: Path, outputs_dir: Path):
        for folder_name in ["figures", "tables"]:  
            src = inputs_dir / folder_name  
            dst = outputs_dir / folder_name

            if not src.exists():  
                print(f"[WARN] missing asset folder: {src}")  
                continue

            if dst.exists():  
                shutil.rmtree(dst)

            shutil.copytree(src, dst)  
            print(f"Copied {src} -> {dst}")

    def _process_table_entry(self, entry: Dict[str, Any], pdf_doc: fitz.Document) -> None:  
        """Replace a table entry with an image reference and save the cropped image.

        Fixes the problem of parsed tables structure being faulty. For now we just save them as images.

        """

        # Extract metadata needed for cropping  
        page_num = entry.get("page number")  
        bbox = entry.get("bounding box")  # [x0, y0, x1, y1]  
        table_id = entry.get("id")

        if page_num is None or bbox is None or table_id is None:  
            return

        # Construct the image filename. In a real implementation we would  
        # generate an image from the PDF using the bbox.  
        img_name = f"table_{table_id}.png"  
        img_path = f"{self.tables_dir}/{img_name}"

        page = pdf_doc[page_num - 1]

        # Y-Inversion (Bottom-Left)  
        #  Standard PDF coordinates start at the bottom-left (0,0). PyMuPDF (and most image libraries) start at the top-left (0,0).  
        page_height = page.rect.height  
        rect = fitz.Rect(bbox[0], page_height - bbox[3], bbox[2], page_height - bbox[1])  
        # rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])

        zoom = 2.0  
        matrix = fitz.Matrix(zoom, zoom)

        pix = page.get_pixmap(matrix=matrix, clip=rect)  
        pix.save(str(img_path))

        # # Replace the table entry with an image-like structure  
        entry.clear()  
        entry.update({  
            "type": "image",  
            "pdfua_tag": "Table",  
            "id": table_id,  
            "page number": page_num,  
            "bounding box": bbox,  
            "alt_source": "missing",  
            "source": f"tables/{img_name}",  
        })

    def _remove_contained_paragraphs(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  
        """

        Removes paragraphs/text blocks that overlap significantly with images/figures - aka whatever was read off of image

        Fixes the problem of parser model reading text seen on images and dropping it as entries of text.

        """  
        # 1. Group image bounding boxes by page number  
        images_by_page = {}  
        for entry in entries:  
            if entry.get("type") == "image":  
                page = entry.get("page number")  
                bbox = entry.get("bounding box")  
                if page is not None and bbox:  
                    images_by_page.setdefault(page, []).append(bbox)

        # 2. Filter out entries that overlap with image bboxes  
        cleaned_entries = []  
        for entry in entries:  
            # We only filter out paragraphs/texts  
            if entry.get("type") in ("paragraph", "list"):  
                page = entry.get("page number")  
                bbox = entry.get("bounding box")

                if page in images_by_page and bbox:  
                    # Check if this paragraph is inside any image on this page  
                    is_artifact = False  
                    for img_bbox in images_by_page[page]:  
                        if self._is_mostly_inside(inner_box=bbox, outer_box=img_bbox, threshold=0.75):  
                            is_artifact = True  
                            break

                    if is_artifact:  
                        # Skip this entry (it's inside an image!)  
                        continue

            cleaned_entries.append(entry)

        return cleaned_entries

    def _is_mostly_inside(self, inner_box: List[float], outer_box: List[float], threshold: float = 0.85) -> bool:  
        """Returns True if 'threshold'% (default 85%) of inner_box overlaps with outer_box.

        Checks if small bbox is mostly inside big bbox. Basically IoU.

        """  
        # Handle cases where coordinates might be inverted (y0 > y1) depending on coordinate space  
        ix_min, ix_max = min(inner_box[0], inner_box[2]), max(inner_box[0], inner_box[2])  
        iy_min, iy_max = min(inner_box[1], inner_box[3]), max(inner_box[1], inner_box[3])

        ox_min, ox_max = min(outer_box[0], outer_box[2]), max(outer_box[0], outer_box[2])  
        oy_min, oy_max = min(outer_box[1], outer_box[3]), max(outer_box[1], outer_box[3])

        # Calculate overlap bounds  
        overlap_x0 = max(ix_min, ox_min)  
        overlap_y0 = max(iy_min, oy_min)  
        overlap_x1 = min(ix_max, ox_max)  
        overlap_y1 = min(iy_max, oy_max)

        # No physical overlap  
        if overlap_x1 <= overlap_x0 or overlap_y1 <= overlap_y0:  
            return False

        # Calculate areas  
        overlap_area = (overlap_x1 - overlap_x0) * (overlap_y1 - overlap_y0)  
        inner_area = (ix_max - ix_min) * (iy_max - iy_min)

        if inner_area <= 0:  
            return False

        # Check if the portion of the paragraph inside the image is higher than our threshold  
        return (overlap_area / inner_area) >= threshold

    def _reorder_figures_and_captions(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  
        """

        Reorder figure images so that matched images appear immediately before their captions.

        Fixes the problem of the pdf images being in random places of the parsed json and also sometimes being duplicated  
        in different locations, but referencing the same image file (model issue). Luckily we have image captions in the right places.

        Matching priority:  
        1) captions with `linked content id`  
        2) same page: figure number == image file number and caption/image ids are adjacent (+/- 1)  
        3) same page: figure number == image file number, choose closest id

        After selecting an image, all duplicate image entries with the same source filename  
        are discarded globally.

        """

        print("\n=== FIGURE + CAPTION MATCHING ON THE OpenDataParser LOGS ===")

        caption_pattern = re.compile(  
            r"^\s*(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
            re.IGNORECASE  
        )  
        image_file_pattern = re.compile(r"imageFile(\d+)\.(?:png|jpg|jpeg)$", re.IGNORECASE)

        # -----------------------------  
        # 1) Gather images and captions by page  
        # -----------------------------  
        images_by_page = defaultdict(list)  
        captions_by_page = defaultdict(list)  
        images_by_id = {}

        for entry in entries:  
            if entry.get("type") == "image" and "figures/" in entry.get("source", ""):  
                page = entry.get("page number")  
                images_by_page[page].append(entry)  
                images_by_id[entry.get("id")] = entry

            elif entry.get("type") in ("caption", "paragraph"):  
                content = entry.get("content", "") or ""  
                match = caption_pattern.match(content)  
                if match:  
                    page = entry.get("page number")  
                    captions_by_page[page].append({  
                        "entry": entry,  
                        "fig_num": int(match.group(1))  
                    })

        # Sort for deterministic behavior  
        for page in images_by_page:  
            images_by_page[page].sort(key=lambda x: x.get("id", 0))  
        for page in captions_by_page:  
            captions_by_page[page].sort(key=lambda x: x["entry"].get("id", 0))

        # -----------------------------  
        # Helpers  
        # -----------------------------  
        def get_image_file_num(img: Dict[str, Any]) -> Optional[int]:  
            src = img.get("source", "") or ""  
            m = image_file_pattern.search(Path(src).name)  
            return int(m.group(1)) if m else None

        def id_distance(caption_entry: Dict[str, Any], image_entry: Dict[str, Any]) -> float:  
            cid = caption_entry.get("id")  
            iid = image_entry.get("id")  
            if isinstance(cid, int) and isinstance(iid, int):  
                return abs(cid - iid)  
            return float("inf")

        def same_source_images(source: str) -> List[Dict[str, Any]]:  
            out = []  
            for page_imgs in images_by_page.values():  
                for img in page_imgs:  
                    if img.get("source") == source:  
                        out.append(img)  
            return out

        # -----------------------------  
        # 2) Match captions with linked content id first  
        # -----------------------------  
        cap_to_img_map = {}  
        used_caption_obj_ids = set()  
        selected_image_obj_ids = set()  
        discarded_image_obj_ids = set()

        for page in sorted(captions_by_page):  
            for cap in captions_by_page[page]:  
                cap_entry = cap["entry"]  
                linked_id = cap_entry.get("linked content id")

                if linked_id is None:  
                    continue

                img = images_by_id.get(linked_id)  
                if img:  
                    cap_to_img_map[cap_entry.get("id")] = img  
                    used_caption_obj_ids.add(cap_entry.get("id"))  
                    selected_image_obj_ids.add(img.get("id"))

                    print(  
                        f"MATCH[linked]: caption ID {cap_entry.get('id')} "  
                        f"(Fig {cap['fig_num']}, page {page}) -> "  
                        f"image ID {img.get('id')} source={img.get('source')}"  
                    )

                    # Discard all duplicate image entries with same source except chosen one  
                    for dup in same_source_images(img.get("source")):  
                        if dup.get("id") != img.get("id"):  
                            discarded_image_obj_ids.add(dup.get("id"))  
                            print(  
                                f"  DISCARD duplicate by source: image ID {dup.get('id')} "  
                                f"page {dup.get('page number')} source={dup.get('source')}"  
                            )

        # -----------------------------  
        # 3) Match remaining captions page by page  
        #    using fig_num == imageFileN and id adjacency (+/-1)  
        # -----------------------------  
        for page in sorted(captions_by_page):  
            page_images = images_by_page.get(page, [])

            for cap in captions_by_page[page]:  
                cap_entry = cap["entry"]  
                cap_obj_id = cap_entry.get("id")

                if cap_obj_id in used_caption_obj_ids:  
                    continue

                fig_num = cap["fig_num"]

                candidates = []  
                for img in page_images:  
                    if img.get("id") in discarded_image_obj_ids or img.get("id") in selected_image_obj_ids:  
                        continue

                    file_num = get_image_file_num(img)  
                    if file_num == fig_num:  
                        if id_distance(cap_entry, img) == 1:  
                            candidates.append(img)

                if candidates:  
                    # if multiple, take the smallest id  
                    chosen = min(candidates, key=lambda img: img.get("id"))  
                    cap_to_img_map[cap_obj_id] = chosen  
                    used_caption_obj_ids.add(cap_obj_id)  
                    selected_image_obj_ids.add(chosen.get("id"))

                    print(  
                        f"MATCH[page+file+adjacent-id]: caption ID {cap_entry.get('id')} "  
                        f"(Fig {fig_num}, page {page}) -> image ID {chosen.get('id')} "  
                        f"source={chosen.get('source')}"  
                    )

                    for dup in same_source_images(chosen.get("source")):  
                        if dup.get("id") != chosen.get("id"):  
                            discarded_image_obj_ids.add(dup.get("id"))  
                            print(  
                                f"  DISCARD duplicate by source: image ID {dup.get('id')} "  
                                f"page {dup.get('page number')} source={dup.get('source')}"  
                            )

        # -----------------------------  
        # 4) Fallback: match remaining captions by page + file number only  
        #    if duplicates on same page, take closest id  
        # -----------------------------  
        for page in sorted(captions_by_page):  
            page_images = images_by_page.get(page, [])

            for cap in captions_by_page[page]:  
                cap_entry = cap["entry"]  
                cap_obj_id = cap_entry.get("id")

                if cap_obj_id in used_caption_obj_ids:  
                    continue

                fig_num = cap["fig_num"]

                candidates = []  
                for img in page_images:  
                    if img.get("id") in discarded_image_obj_ids or img.get("id") in selected_image_obj_ids:  
                        continue

                    file_num = get_image_file_num(img)  
                    if file_num == fig_num:  
                        candidates.append(img)

                if candidates:  
                    chosen = min(candidates, key=lambda img: id_distance(cap_entry, img))  
                    cap_to_img_map[cap_obj_id] = chosen  
                    used_caption_obj_ids.add(cap_obj_id)  
                    selected_image_obj_ids.add(chosen.get("id"))

                    print(  
                        f"MATCH[page+file]: caption ID {cap_entry.get('id')} "  
                        f"(Fig {fig_num}, page {page}) -> image ID {chosen.get('id')} "  
                        f"source={chosen.get('source')}"  
                    )

                    for dup in same_source_images(chosen.get("source")):  
                        if dup.get("id") != chosen.get("id"):  
                            discarded_image_obj_ids.add(dup.get("id"))  
                            print(  
                                f"  DISCARD duplicate by source: image ID {dup.get('id')} "  
                                f"page {dup.get('page number')} source={dup.get('source')}"  
                            )

        # -----------------------------  
        # 5) Warnings  
        # -----------------------------  
        for page in sorted(captions_by_page):  
            for cap in captions_by_page[page]:  
                cap_entry = cap["entry"]  
                if cap_entry.get("id") not in used_caption_obj_ids:  
                    print(  
                        f"WARNING: unmatched caption ID {cap_entry.get('id')} "  
                        f"(Fig {cap['fig_num']}, page {page})"  
                    )

        for page in sorted(images_by_page):  
            for img in images_by_page[page]:  
                if img.get("id") in discarded_image_obj_ids:  
                    continue  
                if img.get("id") not in selected_image_obj_ids:  
                    print(  
                        f"WARNING: unmatched image ID {img.get('id')} "  
                        f"page {page} source={img.get('source')}"  
                    )

        print("=====================================\n")

        # -----------------------------  
        # 6) Reconstruct output:  
        #    - remove discarded duplicates  
        #    - suppress original position of matched images  
        #    - insert matched image immediately before its caption  
        # -----------------------------  
        reordered_entries = []

        for entry in entries:  
            entry_id = entry.get("id")

            # Drop all (figure, not table) images from their original positions  
            # matched ones will be reinserted before caption,  
            # unmatched ones are discarded entirely.  
            if entry.get("type") == "image" and "figures/" in entry.get("source", ""):  
                continue

            # If current entry is a caption with matched image, insert image first  
            if entry_id in cap_to_img_map:  
                reordered_entries.append(cap_to_img_map[entry_id])

            reordered_entries.append(entry)

        return reordered_entries

    def _build_figure_map(self, opendata_entries):  
        mapping = {}

        for i, entry in enumerate(opendata_entries):  
            if entry.get("type") not in ("paragraph", "caption"):  
                continue

            fig_num = self._extract_opendata_figure_number(entry.get("content", ""))  
            if fig_num is None or fig_num in mapping:  
                continue

            source = None  
            for j in range(i - 1, -1, -1):  
                prev = opendata_entries[j]  
                if prev.get("type") == "image" and str(prev.get("pdfua_tag", "")).lower() == "figure":  
                    source = prev.get("source")  
                    break

            if source:  
                mapping[fig_num] = source

        return mapping

    def _build_table_map(self, opendata_entries):  
        mapping = {}

        for i, entry in enumerate(opendata_entries):  
            if entry.get("type") != "paragraph":  
                continue

            table_num = self._extract_opendata_table_number(entry.get("content", ""))  
            if table_num is None or table_num in mapping:  
                continue

            source = None  
            for j in range(i + 1, len(opendata_entries)):  
                nxt = opendata_entries[j]  
                if nxt.get("type") == "image" and str(nxt.get("pdfua_tag", "")).lower() == "table":  
                    source = nxt.get("source")  
                    break

            if source:  
                mapping[table_num] = source

        return mapping

    def _extract_nuext_figure_number_from_html(self, html):  
        m = re.search(  
            r"<figcaption\b[^>]*>.*?\b(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
            html,  
            flags=re.IGNORECASE | re.DOTALL,  
        )  
        return int(m.group(1)) if m else None

    def _extract_nuext_table_number_from_html(self, html):  
        m = re.search(  
            r"<caption\b[^>]*>.*?\btable\s+(\d+)\b",  
            html,  
            flags=re.IGNORECASE | re.DOTALL,  
        )  
        return int(m.group(1)) if m else None

    def _extract_nuext_figure_number(self, entries, i):  
        entry = entries[i]  
        html = entry.get("content", "") or ""

        # 1) Try figure HTML first  
        fig_num = self._extract_nuext_figure_number_from_html(html)  
        if fig_num is not None:  
            return fig_num

        # 2) Fallback to previous paragraph  
        if i > 0:  
            prev = entries[i - 1]  
            if prev.get("type") == "paragraph":  
                return self._extract_opendata_figure_number(prev.get("content", ""))

        return None

    def _extract_nuext_table_number(self, entries, i):  
        entry = entries[i]  
        html = entry.get("content", "") or ""

        # 1) Try table HTML first  
        table_num = self._extract_nuext_table_number_from_html(html)  
        if table_num is not None:  
            return table_num

        # 2) Fallback to previous paragraph  
        if i > 0:  
            prev = entries[i - 1]  
            if prev.get("type") == "paragraph":  
                return self._extract_opendata_table_number(prev.get("content", ""))

        return None

    def _extract_opendata_figure_number(self, text):  
        text = self._strip_md(text)  
        m = re.search(  
            r"^\s*(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return int(m.group(1)) if m else None

    def _extract_opendata_table_number(self, text):  
        text = self._strip_md(text)  
        m = re.search(  
            r"^\s*table\s+(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return int(m.group(1)) if m else None

    def _strip_md(self, text):  
        text = text.replace("**", "").replace("__", "")  
        text = text.replace("*", "").replace("_", "")  
        text = re.sub(r"\s+", " ", text)  
        return text.strip()