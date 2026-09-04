import json  
import re  
from pathlib import Path

import utils

import tokenizer


class ChapterSplitter:  
    """  
    Split extracted PDF blocks into coarse article sections using  
    heading blocks plus paragraph-based heuristics.  
    """

    def __init__(self, outputs_path: str):
        self.outdir = Path(outputs_path) / "chapters"  
        self.outdir.mkdir(parents=True, exist_ok=True)  
        self.tokenizer = tokenizer

    def split(self, infile: str):  
        with open(infile, "r", encoding="utf-8") as f:  
            blocks = json.load(f)

        if not blocks:  
            return []

        title = self._get_document_title(blocks, infile)

        # Find explicit section starts  
        starts = []  
        for i, block in enumerate(blocks):  
            section = self.classify_section_start(block)
            if section:  
                starts.append((i, section))

        # Add fallback abstract/introduction if needed  
        starts = self._inject_missing_front_sections(blocks, starts)

        # If still nothing useful, just dump all as one introduction  
        if not starts:  
            starts = [(0, "introduction")]

        # Deduplicate and sort  
        starts = self._dedupe_and_sort_starts(starts)

        files = self._build_section_files(blocks, starts, title)

        for i, item in enumerate(files, 1):  
            heading = item["document"]["sections"][0]["heading"]  
            name = re.sub(r"[^A-Za-z0-9_-]+", "_", heading).strip("_")  
            outpath = self.outdir / f"{i:02d}_{name}.json"  
            with open(outpath, "w", encoding="utf-8") as f:  
                json.dump(item, f, indent=2, ensure_ascii=False)

        #return files

    def _get_document_title(self, blocks, infile):  
        for b in blocks:  
            if b.get("type") == "title" and b.get("content", "").strip():
                return b["content"].strip()  
        return Path(infile).stem

    def classify_section_start(self, block):  
        content = block.get("content", "")  
        block_type = block.get("type")
        page = block.get("page")

        if not content.strip():  
            return None

        normalized = self.normalize_heading(content)

        # Real headings: detect normal sections  
        if block_type == "heading":  
            if normalized in {"abstract"}:  
                return "abstract"  
            if normalized in {"intro", "introduction"}:  
                return "introduction"  
            if self._contains_heading_word(normalized, {"method", "methods", "methodology"}):  
                return "methods"  
            if self._contains_heading_word(normalized, {"result", "results"}):  
                return "results"  
            if self._contains_heading_word(normalized, {"discussion"}):  
                return "discussion"  
            if self._contains_heading_word(normalized, {"conclusion", "conclusions"}):  
                return "conclusion"
            if self._contains_heading_word(normalized, {"references", "bibliography"}):
                return "references"

        # Paragraph bold markers: only infer ABSTRACT  
        if block_type == "paragraph" and page == 1:  
            bold_lead = self._extract_bold_lead(content)  
            if bold_lead:  
                bold_norm = self.normalize_heading(bold_lead)  
                if bold_norm in {  
                    "abstract",  
                    "purpose",  
                    "background",  
                    "objective",  
                    "objectives",  
                    "study design",  
                    "materials and methods",  
                    "methods",  
                    "results",  
                    "conclusion",  
                    "conclusions",  
                }:  
                    return "abstract"

        # Paragraph bold markers: only infer ABSTRACT  
        if block_type == "paragraph" and page == 1:  
            bold_lead = self._extract_bold_lead(content)  
            if bold_lead:  
                bold_norm = self.normalize_heading(bold_lead)
                if bold_norm in {  
                    "key words",
                    "keywords"
                }:  
                    return "introduction"

        return None  

    def _inject_missing_front_sections(self, blocks, starts):  
        sections_present = {section for _, section in starts}

        # Find first methods index if any  
        methods_idx = None  
        for idx, section in sorted(starts, key=lambda x: x[0]):  
            if section == "methods":  
                methods_idx = idx  
                break

        if "abstract" not in sections_present and "introduction" not in sections_present:  
            first_page_idxs = [  
                i for i, b in enumerate(blocks)  
                if b.get("page") == 1  
            ]

            if first_page_idxs:  
                first_page_start = min(first_page_idxs)  
                starts.append((first_page_start, "abstract"))

            if methods_idx is not None:  
                intro_idx = self._first_index_after_page(blocks, 1)  
                if intro_idx is not None and intro_idx < methods_idx:  
                    starts.append((intro_idx, "introduction"))

        elif "abstract" in sections_present and "introduction" not in sections_present:  
            if methods_idx is not None:  
                abstract_idx = min(idx for idx, sec in starts if sec == "abstract")  
                candidate_intro = abstract_idx + 1  
                if candidate_intro < methods_idx:  
                    starts.append((candidate_intro, "introduction"))

        return starts

    def _first_index_after_page(self, blocks, page_num):  
        for i, b in enumerate(blocks):  
            p = b.get("page")  
            if isinstance(p, int) and p > page_num:  
                return i  
        return None

    def _dedupe_and_sort_starts(self, starts):  
        # Sort by index first; if multiple labels land on same index,  
        # keep the first one encountered.  
        starts = sorted(starts, key=lambda x: x[0])
        deduped = []  
        seen_idx = set()

        for idx, sec in starts:  
            if idx in seen_idx:  
                continue  
            deduped.append((idx, sec))  
            seen_idx.add(idx)

        # Remove out-of-order duplicate sections later if desired.  
        # For now, keep first occurrence of each section in reading order.
        final = []  
        seen_section = set()  
        for idx, sec in deduped:  
            if sec in seen_section:  
                continue  
            final.append((idx, sec))  
            seen_section.add(sec)

        return final

    def _build_section_files(self, blocks, starts, title):  
        files = []

        for pos, (start_idx, section_name) in enumerate(starts):  
            end_idx = starts[pos + 1][0] if pos + 1 < len(starts) else len(blocks)  
            kids = []

            for b in blocks[start_idx:end_idx]:  
                item = dict(b)  
                if self.tokenizer is not None:  
                    item["tokens"] = self.tokenizer.count_tokens(item.get("content", ""))  
                kids.append(item)

            files.append({  
                "document": {  
                    "title": title,  
                    "sections": [{  
                        "heading": section_name,  
                        "kids": kids  
                    }]  
                }  
            })

        return files

    def _extract_bold_lead(self, text):  
        """  
        Extract leading markdown-bold label, e.g.  
        '**Results.** some text' -> 'Results.'  
        """  
        m = re.match(r"^\s*\*\*(.+?)\*\*", text)  
        if m:  
            return m.group(1).strip()  
        return None

    def _contains_heading_word(self, normalized_text, targets):  
        words = normalized_text.split()  
        return any(w in targets for w in words[:3])


    def normalize_heading(self, text):  
        text = utils._strip_md(text)  
        text = text.lower().strip()  
        text = re.sub(r"[^a-z0-9]+", " ", text)  
        text = re.sub(r"\s+", " ", text).strip()  
        return text  