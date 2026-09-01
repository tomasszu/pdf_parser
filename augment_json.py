# This module defines the CleanupJSON class used to sanitize parsed PDF JSON outputs.

import json  
import re  
import shutil  
from pathlib import Path  
from typing import List, Dict, Any, Optional  , Tuple
from collections import defaultdict
import html as html_lib

import fitz  # PyMuPDF


class AugmentJSON:  
    """Utility class to combine and augment the JSON representation of parsed PDFs from both NuExtract and Opendataloader parsers.

    Fixes the problem of NuExtract parsed pdf json being imperfect.

    """

    def __init__(self, images_dir: Path):
        # pages of images folder  
        self.images_dir = images_dir  

    def run(self, nuext_input_json: Path) -> None:
        """Process, sort, crop tables, and clean up artifact paragraphs inside images, align images with captions."""

        # Load JSON data  
        with open(nuext_input_json, "r", encoding="utf-8") as f:  
            cleaned_entries = json.load(f)

        json_folder = nuext_input_json.parent

        # <<<<<<<<<<<<<<<<<<<<<<<<The following deals with merging the images of figures and tables into the nuextract cleaned up json.>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

        print("\n=======================================================")
        print("\n=== Building map of Docling extracted images ===")
        table_map, figure_map = self.build_docling_asset_maps(self.images_dir)

        print("Tables from Docling:", table_map)
        print("Figures from Docling:", figure_map)

        print("\n=======================================================")  
        print("\n=== Finding Tables and Figures in the NuExtract json and matching them against docling images maps ===")

        out = []  
        for i, entry in enumerate(cleaned_entries):  
            e = dict(entry)

            if e.get("type") == "figure":  
                figure = self._extract_nuext_figure_number(cleaned_entries, i)
                fig_num = figure["number"]
                caption = figure["caption"]

                if fig_num in figure_map:  
                    e["content"] = figure_map[fig_num]
                    e["caption"] = caption
                    print(f"[OK] matched figure {fig_num}")  
                else:  
                    print(f"[MISS] figure {fig_num}")

            elif e.get("type") == "table":  
                table = self._extract_nuext_table_number(cleaned_entries, i)
                table_num = table["number"]
                caption = table["caption"]

                if table_num in table_map:  
                    e["content"] = table_map[table_num]
                    e["caption"] = caption
                    print(f"[OK] matched table {table_num}")  
                else:  
                    print(f"[MISS] table {table_num}")
                    print(f"  missing content preview: {repr((e.get('content', '') or '')[:500])}")

            out.append(e)

        output_path = json_folder / "combined_blocks_augmented.json"
        with open(output_path, "w", encoding="utf-8") as f:  
            json.dump(out, f, indent=2, ensure_ascii=False)


    def _extract_nuext_figure_number_from_html(self, html):  
        m = re.search(  
            r"<figcaption\b[^>]*>.*?\b(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
            html,  
            flags=re.IGNORECASE | re.DOTALL,  
        )

        caption_match = re.search(  
            r"<figcaption\b[^>]*>(.*?)</figcaption>",  
            html,  
            flags=re.IGNORECASE | re.DOTALL,  
        )  
        caption = None  
        if caption_match:  
            caption = re.sub(r"<[^>]+>", "", caption_match.group(1)).strip()  
            caption = html_lib.unescape(caption)

        return {  
            "number": int(m.group(1)) if m else None,  
            "caption": caption if caption else None,
        }


    def _extract_nuext_table_number_from_html(self, html):  
        m = re.search(  
            r"<caption\b[^>]*>.*?\btable\s+(\d+)\b",  
            html,  
            flags=re.IGNORECASE | re.DOTALL,  
        )

        caption_match = re.search(  
            r"<caption\b[^>]*>(.*?)</caption>",  
            html,  
            flags=re.IGNORECASE | re.DOTALL,  
        )  
        caption = None  
        if caption_match:  
            caption = re.sub(r"<[^>]+>", "", caption_match.group(1)).strip()  
            caption = html_lib.unescape(caption)

        return {  
            "number": int(m.group(1)) if m else None,  
            "caption": caption if caption else None,  
        }

    def _extract_nuext_figure_number(self, entries, i):  
        entry = entries[i]  
        html = entry.get("content", "") or ""

        # 1) Try figure HTML first  
        figure = self._extract_nuext_figure_number_from_html(html)  
        if figure["number"] is not None:  
            return figure

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
        table = self._extract_nuext_table_number_from_html(html)  
        if table["number"] is not None:  
            return table

        # 2) Fallback to previous paragraph  
        if i > 0:  
            prev = entries[i - 1]  
            if prev.get("type") == "paragraph":  
                return self._extract_opendata_table_number(prev.get("content", ""))

        return None

    def build_docling_asset_maps(self, pages_dir: str | Path) -> Tuple[Dict[int, str], Dict[int, str]]:  
        """  
        Traverse a Docling pages directory and return:  
        - tables_map: {table_number: relative_path_after_pages}  
        - images_map: {figure_number: relative_path_after_pages}

        Example:  
            input file:  .../pages/8/tables/4.png  
            output val:  "8/tables/4.png"  
        """  
        pages_dir = Path(pages_dir).resolve()

        tables_map: Dict[int, str] = {}  
        images_map: Dict[int, str] = {}

        if not pages_dir.exists():  
            raise FileNotFoundError(f"Pages directory does not exist: {pages_dir}")

        for file_path in pages_dir.rglob("*.png"):  
            rel_parts = file_path.relative_to(pages_dir).parts

            # Expecting: page_number / category / filename  
            # Example: ("8", "tables", "4.png")  
            if len(rel_parts) != 3:  
                continue

            page_part, category, filename = rel_parts

            if category not in {"tables", "images"}:  
                continue

            stem = Path(filename).stem

            # Ignore files like no_cap_1.png  
            if not stem.isdigit():  
                continue

            asset_number = int(stem)  
            rel_path = file_path.relative_to(pages_dir).as_posix()

            target_map = tables_map if category == "tables" else images_map

            if asset_number in target_map:  
                print(  
                    f"Warning: duplicate {category[:-1]} number {asset_number}. "  
                    f"Overwriting {target_map[asset_number]} with {rel_path}"  
                )

            target_map[asset_number] = rel_path

        return tables_map, images_map

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


    def _extract_opendata_table_number(self, text):  
        text = self._strip_md(text)  
        m = re.search(  
            r"^\s*table\s+(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return {  
            "number": int(m.group(1)) if m else None,  
            "caption": text.strip() if text else None,  
        }

    def _strip_md(self, text):  
        text = text.replace("**", "").replace("__", "")  
        text = text.replace("*", "").replace("_", "")  
        text = re.sub(r"\s+", " ", text)  
        return text.strip()