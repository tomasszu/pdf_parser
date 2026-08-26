# This module defines the CleanupJSON class used to sanitize parsed PDF JSON outputs.

import json
import re
from pathlib import Path
from typing import Dict, Any
import shutil

from typing import Dict, Any, List  


import fitz # PyMuPDF

class AugmentJSON:
    """Utility class to combine and augment the JSON representation of parsed PDFs from both NuExtract and Opendataloader parsers.
    
    Fixes the problem of parsed pdf json being imperfect.
    """

    def __init__(self, output_dir: Path):
        # output folder
        self.output_dir = output_dir
        # cropped tables images folder
        self.tables_dir = f"{output_dir}/opendata_parser/tables"
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
        with open(opendata_input_json, "w", encoding="utf-8") as f:  
            json.dump(opendata_data, f, indent=2, ensure_ascii=False)

        # <<<<<<<<<<<<<<<<<<<<<<<<The following deals with merging the images of figures and tables into the nuextract cleaned up json.>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

        output_folders_path = Path(nuext_input_json).parent
        input_folders_path = Path(opendata_input_json).parent

        opendata_entries = opendata_data["kids"]

        figure_map = self._build_figure_map(opendata_entries)  
        table_map = self._build_table_map(opendata_entries)

        print("\nFigure matches:")  
        for k, v in figure_map.items():  
            print(f"  {k} -> {v}")

        print("\nTable matches:")  
        for k, v in table_map.items():  
            print(f"  {k} -> {v}")

        out = []
        for i, entry in enumerate(cleaned_entries):  
            e = dict(entry)

            if e.get("type") == "figure":  
                fig_num = self._extract_figure_number_from_html(e.get("content", ""))
                if fig_num in figure_map:  
                    e["content"] = figure_map[fig_num]  
                    print(f"[OK] matched figure {fig_num}")  
                else:  
                    print(f"[MISS] figure {fig_num}")

            elif e.get("type") == "table":  
                table_num = self._extract_table_number_from_html(e.get("content", ""))

                if table_num is None and i > 0 and cleaned_entries[i - 1].get("type") == "paragraph":  
                    table_num = self._extract_table_number(cleaned_entries[i - 1].get("content", ""))

                if table_num in table_map:  
                    e["content"] = table_map[table_num]  
                    print(f"[OK] matched table {table_num}")  
                else:  
                    print(f"[MISS] table {table_num}")

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

        page = pdf_doc[page_num-1]

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
        """Removes duplicate images (keeping smallest ID) and matches unique figures sequentially to captions. Inserts images just before their captions.
        
        Fixes the problem of the pdf images being in random places of the parsed json and also sometimes being duplicated
        in different locations, but referencing the same image file (model issue). Luckily we have image captions in the right places.
        
        """  

        # 1. Extract and sort figure images by ID, de-duplicating on the fly  
        raw_images = sorted(  
            [e for e in entries if e.get("type") == "image" and "figures/" in e.get("source", "")],
            key=lambda x: x.get("id", 0)  
        )

        unique_images = []  
        seen_sources = set()  
        discarded_img_ids = set()

        print("\n=== DEDUPLICATION & MATCHING LOGS ===")
        for img in raw_images:  
            source = img.get("source")  
            if source not in seen_sources:  
                seen_sources.add(source)  
                unique_images.append(img)  
            else:  
                discarded_img_ids.add(id(img))  
                print(f"REMOVED DUPLICATE: '{source}' (ID: {img.get('id')} on Page {img.get('page number')})")

        # Index unique images sequentially (1-based)  
        indexed_images = {i + 1: img for i, img in enumerate(unique_images)}


        # 2. Gather all captions and extract their numbers  
        caption_pattern = re.compile(r"^\s*(?:Figure|Fig\.?)\s*(\d+)", re.IGNORECASE)  
        captions = []  
        for entry in entries:  
            if entry.get("type") in ("paragraph", "list"):  
                content = entry.get("content", "")  
                match = caption_pattern.match(content)  
                if match:  
                    captions.append({"entry": entry, "num": int(match.group(1))})


        # 3. Match sequential unique images to caption numbers  
        cap_to_img_map = {}  
        matched_img_ids = set()

        for cap in captions:  
            num = cap["num"]  
            cap_id = cap["entry"].get("id")  
            if num in indexed_images:  
                img = indexed_images[num]  
                cap_to_img_map[id(cap["entry"])] = img  
                matched_img_ids.add(id(img))  
                print(f"MATCH: Caption {num} (ID: {cap_id}) <-> Image Index {num} (ID: {img.get('id')})")  
            else:  
                print(f"WARNING: Caption {num} (ID: {cap_id}) has no corresponding unique image index.")

        # Print warnings for leftover unmatched images  
        for idx, img in indexed_images.items():  
            if id(img) not in matched_img_ids:  
                print(f"WARNING: Unique Image Index {idx} (ID: {img.get('id')}) has no matching caption.")  
        print("=====================================\n")

        # 4. Reconstruct list: place matched figures directly before their captions  
        reordered_entries = []  
        for entry in entries:

            # Skip discarded duplicate images completely  
            if id(entry) in discarded_img_ids:  
                continue

            # Skip matched images if its their original misplaced index position (don't input them in the dataframe just yet)
            if id(entry) in matched_img_ids:
                continue

            # If current entry is a matched caption, insert the linked image first  (Paste image now)
            if id(entry) in cap_to_img_map:  
                reordered_entries.append(cap_to_img_map[id(entry)])

            # insert whatever else entry exists in the dataframe
            reordered_entries.append(entry)

        return reordered_entries

    def _build_figure_map(self, nuext_entries):  
        mapping = {}

        for i, entry in enumerate(nuext_entries):  
            if entry.get("type") != "paragraph":  
                continue

            fig_num = self._extract_figure_number(entry.get("content", ""))  
            if fig_num is None:  
                continue

            source = None  
            for j in range(i - 1, -1, -1):  
                prev = nuext_entries[j]  
                if prev.get("type") == "image" and str(prev.get("pdfua_tag", "")).lower() == "figure":  
                    source = prev.get("source")  
                    break

            if source:  
                mapping[fig_num] = source

        return mapping

    def _build_table_map(self, nuext_entries):  
        mapping = {}

        for i, entry in enumerate(nuext_entries):  
            if entry.get("type") != "paragraph":  
                continue

            table_num = self._extract_table_number(entry.get("content", ""))  
            if table_num is None:  
                continue

            source = None  
            for j in range(i + 1, len(nuext_entries)):  
                nxt = nuext_entries[j]  
                if nxt.get("type") == "image" and str(nxt.get("pdfua_tag", "")).lower() == "table":  
                    source = nxt.get("source")  
                    break

            if source:  
                mapping[table_num] = source

        return mapping

    def _extract_figure_number_from_html(self, html):  
        m = re.search(r"<figcaption.*?>.*?\bFigure\s+(\d+)\b", html, flags=re.IGNORECASE | re.DOTALL)  
        if m:  
            return int(m.group(1))  
        return None

    def _extract_table_number_from_html(self, html):  
        m = re.search(r"<caption.*?>.*?\bTable\s+(\d+)\b", html, flags=re.IGNORECASE | re.DOTALL)
        if m:  
            return int(m.group(1))  
        return None

    def _extract_figure_number(self, text):  
        m = re.search(r"\bFigure\s+(\d+)\b", text, flags=re.IGNORECASE)  
        if m:  
            return int(m.group(1))  
        return None

    def _extract_table_number(self, text):  
        text = self._strip_md(text)  
        m = re.search(r"\bTable\s+(\d+)\b", text, flags=re.IGNORECASE)
        if m:  
            return int(m.group(1))  
        return None

    def _strip_md(self, text):  
        text = text.replace("**", "").replace("__", "")  
        text = text.replace("*", "").replace("_", "")  
        text = re.sub(r"\s+", " ", text)  
        return text.strip()
