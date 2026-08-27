import json  
import re  
from pathlib import Path


class JSONPostProcessor:
        
    """
    Aims to relieve 3 things: a) header and footer noise, b) logo images noise, c) paragraph fragmentation in case of page break or structural break.
    """

    def __init__(self, output_path):
        self.output_path = Path(output_path)
        self.output_folder = self.output_path.parent

    def run(self, input_json, output_name="combined_blocks_clean.json"):  
        input_json = Path(input_json)

        with input_json.open("r", encoding="utf-8") as f:  
            entries = json.load(f)

        entries = self._remove_header_footer_noise(entries)  
        entries = self._remove_global_artifacts(entries)
        entries = self._remove_logo_figures(entries)  
        entries = self._merge_split_paragraphs(entries)


        self.output_folder.mkdir(parents=True, exist_ok=True)  

        with self.output_path.open("w", encoding="utf-8") as f:  
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def _remove_header_footer_noise(self, entries):  
        pages = self._group_entries_by_page(entries)
        cleaned = []

        for page_num in sorted(pages):  
            page_entries = pages[page_num]

            # if is first page we look for "metadata" to clean up in the whole page
            if page_num == 1:  
                page_entries = [  
                    e for e in page_entries  
                    if not (  
                        e.get("type") == "paragraph"  
                        and self._is_first_page_metadata(self._plain_text(e))  
                    )  
                ]

            # possible header and footer on this page
            first_idx = self._find_first_paragraph_index(page_entries)  
            last_idx = self._find_last_paragraph_index(page_entries)

            to_remove = set()

            # check if truly is header / footer
            if first_idx is not None:
                first_text = self._plain_text(page_entries[first_idx])
                if self._is_header_footer_candidate(first_text) and self._word_count(first_text) < 10:
                    to_remove.add(first_idx)

            if last_idx is not None and last_idx != first_idx:
                last_text = self._plain_text(page_entries[last_idx])
                if self._is_header_footer_candidate(last_text) and self._word_count(last_text) < 10:
                    to_remove.add(last_idx)

            for i, entry in enumerate(page_entries):  
                if i not in to_remove:  
                    cleaned.append(entry)

        return cleaned

    def _remove_global_artifacts(self, entries):
        # remove "artifacts" like urls from anywhere in the document, not just 1st page or headers
        cleaned = []

        for entry in entries:  
            if entry.get("type") == "paragraph":  
                if self._is_global_artifact(entry.get("content", "")):
                    continue  
            cleaned.append(entry)

        return cleaned

    def _remove_logo_figures(self, entries):  
        cleaned = []

        for entry in entries:  
            if entry.get("type") != "figure":  
                cleaned.append(entry)  
                continue

            if self._is_logo_figure(entry.get("content", "")):  
                continue

            cleaned.append(entry)

        return cleaned

    def _merge_split_paragraphs(self, entries):
        # Fixes problem of paragraphs being split apart by page break or other structural breaks that were read off of paper
        merged = []  
        i = 0

        while i < len(entries):  
            entry = entries[i]

            # Skipe tables, headings etc
            if entry.get("type") != "paragraph":  
                merged.append(entry)  
                i += 1  
                continue
            # Skip paragraphs that start with emphasis - soft heading
            elif entry.get("content", "").lstrip().startswith("*"):
                merged.append(entry)
                i += 1
                continue

            # get textual content of the block
            current_text = entry.get("content", "")

            # If paragraph doesent end broken or is short(possibly a footnote)
            if not self._ends_broken(current_text) or self._word_count(current_text) <= 10:
                merged.append(entry)  
                i += 1  
                continue

            # if we found a paragraph that ends broken - > start querying the succeeding paragraphs for a continuation.
            j = i + 1
            intervening = []

            while j < len(entries):  
                candidate = entries[j]

                # dont taKE non-paragraph entries into account.
                if candidate.get("type") != "paragraph":  
                    intervening.append(candidate)  
                    j += 1  
                    continue

                # also no emphasis paragraphs
                if candidate.get("content", "").lstrip().startswith("*"):
                    intervening.append(candidate)  
                    j += 1  
                    continue

                break # we have found a simple paragraph to check if its the continuation

            if j < len(entries):
                next_entry = entries[j]  
                next_text = next_entry.get("content", "")

                if (  
                    self._starts_broken(next_text)  
                    and self._word_count(next_text) > 10  # attempt at possible header exclusion
                ):  
                    new_entry = dict(entry)  
                    new_entry["content"] = self._join_paragraphs(current_text, next_text)  
                    merged.append(new_entry)  
                    merged.extend(intervening)  
                    i = j + 1  
                    continue

            merged.append(entry)  
            merged.extend(intervening)
            i = j

        return merged

    def _ends_broken(self, text):
        # looking if string ends without proper punctuation 
        text = text.rstrip()

        if not text:  
            return False

        if text.endswith(("-", ",")):  
            return True

        if text.endswith((".", "!", "?")):  
            return False

        # headings might look broken, so we check that text is longer than 3 words
        return self._word_count(text) > 3

    def _starts_broken(self, text):
        # check if text begins in lowercase
        text = text.lstrip()

        if not text:  
            return False

        return text[0].islower()

    def _join_paragraphs(self, left, right):  
        left = left.rstrip()  
        right = right.lstrip()

        if left.endswith("-"):  
            return left[:-1] + right

        return left + " " + right

    def _is_logo_figure(self, content):
        # remove figure entries that state that they are logos or captions or creastive commons watermark in the figure caption
        lower = content.lower()  
        if "<figcaption" in lower:  
            return False  
        return ("logo" in lower) or ("creative commons" in lower) or ("icon" in lower)

    def _plain_text(self, entry):  
        return self._strip_md(entry.get("content", "")).strip()

    def _strip_md(self, text):  
        text = re.sub(r"!\[.*?\]\(.*?\)", " ", text)  
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  
        text = text.replace("**", "").replace("__", "")  
        text = text.replace("*", "").replace("_", "")  
        text = re.sub(r"\s+", " ", text)  
        return text.strip()

    def _word_count(self, text):  
        plain = self._strip_md(text)  
        return len(re.findall(r"\b\w+\b", plain))

    def _group_entries_by_page(self, entries):  
        pages = {}  
        for entry in entries:  
            pages.setdefault(entry.get("page"), []).append(entry)  
        return pages

    def _find_first_paragraph_index(self, page_entries):  
        for i, entry in enumerate(page_entries):  
            if entry.get("type") == "paragraph":  
                return i  
        return None

    def _find_last_paragraph_index(self, page_entries):  
        for i in range(len(page_entries) - 1, -1, -1):  
            if page_entries[i].get("type") == "paragraph":  
                return i  
        return None

    def _is_first_page_metadata(self, text):
        # a bunch of criteria to determine if text is part of title page clutter
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return False

        # Simple artifacts: journal names, loose text and pseudo-headers
        if tl in {  
            "original article",  
            "review article",  
            "case report",  
            "editorial",  
            "letter to the editor",  
            "global spine journal",  
            "spine deformity",  
            "spine",  
        }:  
            return True

        # Journal/proceedings vol. numbers
        if re.match(r"^\d{4},?\s*vol\.?\s*\d+\(?\d+\)?\s+\d+[–-]\d+$", t, flags=re.IGNORECASE):  
            return True
        # "@ the authors" kind of signatures
        if re.match(r"^©\s*the author\(s\)\s*\d{4}$", t, flags=re.IGNORECASE):  
            return True
        # clutter
        if "creative commons" in tl and "license" in tl:  
            return True
        # general clutter definitions
        if self._is_metadata_paragraph(t):
            return True

        return False

    def _is_header_footer_candidate(self, text):  
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return False

        # digits and "journal" anywhere in string
        if re.match(r"^(?=.*\d)(?=.*\bjournal\b).*", t, flags=re.IGNORECASE):
            return True

        if self._is_metadata_paragraph(t):
            return True

        # some sort of digits leading a string up to 8 words
        if re.match(r"^\d+\b", t) and len(t.split()) <= 8:  
            return True

        return False

    def _is_metadata_paragraph(self, text):  
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return True
        # structural elements like line dividers
        if re.fullmatch(r"[*•·\-_=]{3,}", t):  
            return True

        # urls
        if "www." in tl or "http://" in tl or "https://" in tl:  
            return True

        #urls
        if any(domain in tl for domain in [".com", ".org", ".edu", ".cn", ".gov"]):  
            return True
        
        # signoffs
        if "et al" in tl:  
            return True

        # clutter
        if "copyright" in tl:  
            return True
        # clutter
        if "all rights reserved" in tl:  
            return True
        # clutter
        if re.match(r"^©", t, flags=re.IGNORECASE):
            return True
        # journal names / pseudo-headings
        if tl in {"spine", "spine deformity", "global spine journal"}:  
            return True

        return False

    def _is_global_artifact(self, text):  
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return True

        if re.fullmatch(r"(.)\1+", t):
            return True

        if "www." in tl or "http://" in tl or "https://" in tl:  
            return True

        if "copyright" in tl:  
            return True

        if "all rights reserved" in tl:  
            return True

        if tl in {"spine", "spine deformity", "global spine journal", "springer"}:
            return True

        return False