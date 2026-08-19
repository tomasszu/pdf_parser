import json  
import re  
from pathlib import Path  
from statistics import quantiles

import tokenizer



TARGET_HEADINGS = {  
    "abstract",  
    "intro",  
    "introduction",  
    "methods",  
    "results",  
    "discussion",  
    "conclusion",  
}


class ChapterSplitter:  
    """  
    Class to use when dealing with the entries extracted from a pdf doxument.  
    """

    def __init__(self, outputs_path: str):  
        self.outdir = Path(f"{outputs_path}/chapters")  
        self.outdir.mkdir(parents=True, exist_ok=True)

        # Regex for paragraph headings:  
        # first word must be one of the target headings  
        pattern = r"^\s*(?:" + "|".join(re.escape(h) for h in sorted(TARGET_HEADINGS, key=len, reverse=True)) + r")\b"
        self.target_heading_regex = re.compile(pattern, re.IGNORECASE)

    def split(self, infile: str):  
        with open(infile, "r", encoding="utf-8") as f:  
            doc = json.load(f)

        blocks = doc["kids"]

        if not any(isinstance(b.get("font size"), (int, float)) for b in blocks):  
            print("Text blocks don't include font sizes - cannot execute logic. Returning.")  
            return

        font_size_threshold = self.aggregate_font_sizes(blocks)

        files = []  
        current = None

        for b in blocks[1:]:
            heading = self.is_section_heading(b, font_size_threshold) 
            if heading:
                if current:  
                    files.append(current)

                current = {  
                    "document": {  
                        "title": doc["file name"],  
                        "sections": [{  
                            "heading": heading,
                            "kids": []  
                        }]  
                    }  
                }

            elif current:
                ### can be located elsewhere, but inserted here for convenience - adding token count for each text block, since next steps include chunking
                b["tokens"] = tokenizer.count_tokens(b.get("content", ""))
                
                current["document"]["sections"][0]["kids"].append(b)

        if current:  
            files.append(current)

        for i, item in enumerate(files, 1):  
            heading = item["document"]["sections"][0]["heading"]  
            name = re.sub(r"[^A-Za-z0-9_-]+", "_", heading).strip("_")  
            outpath = self.outdir / f"{i:02d}_{name}.json"  
            with open(outpath, "w", encoding="utf-8") as f:  
                json.dump(item, f, indent=2, ensure_ascii=False)

    def is_section_heading(self, block, font_size_threshold):  
        content = block.get("content", "")  
        normalized = self.normalize_heading(content)  
        block_type = block.get("type")  
        font_size = block.get("font size")

        if block_type == "heading" and normalized in TARGET_HEADINGS:  
            return normalized

        if (  
            block_type == "paragraph"  
            and isinstance(font_size, (int, float))  
            and font_size > font_size_threshold  
        ):  
            match = self.target_heading_regex.match(content)  
            if match:  
                matched = self.normalize_heading(match.group(0))  
                if matched in TARGET_HEADINGS:  
                    return matched

        return None

    def normalize_heading(self, text):  
        text = text.lower().strip()  
        text = re.sub(r"[^a-z0-9]+", " ", text)  
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def aggregate_font_sizes(self, blocks):
        font_sizes = [  
            b.get("font size")  
            for b in blocks  
            if isinstance(b.get("font size"), (int, float))  
        ]  
        if len(font_sizes) >= 2:  
            font_size_threshold = quantiles(font_sizes, n=4)[2]  
        else:  
            font_size_threshold = 0

        return font_size_threshold