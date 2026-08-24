import json  
import re  
from pathlib import Path


class JSONPostProcessor:  
    def __init__(self, outputs_path):  
        self.outputs_path = Path(outputs_path)

    def run(self, input_json, output_name="combined_blocks_clean.json"):  
        input_json = Path(input_json)

        with input_json.open("r", encoding="utf-8") as f:  
            entries = json.load(f)

        entries = self._remove_header_footer_noise(entries)  
        entries = self._remove_global_noise_paragraphs(entries)  
        entries = self._remove_logo_figures(entries)  
        entries = self._merge_split_paragraphs(entries)

        self.outputs_path.mkdir(parents=True, exist_ok=True)  
        output_file = self.outputs_path / output_name

        with output_file.open("w", encoding="utf-8") as f:  
            json.dump(entries, f, ensure_ascii=False, indent=2)

        return entries

    def _remove_header_footer_noise(self, entries):  
        pages = self._group_entries_by_page(entries)  
        cleaned = []

        for page_num in sorted(pages):  
            page_entries = pages[page_num]

            if page_num == 1:  
                page_entries = [  
                    e for e in page_entries  
                    if not (  
                        e.get("type") == "paragraph"  
                        and self._is_first_page_metadata(self._plain_text(e))  
                    )  
                ]

            first_idx = self._find_first_paragraph_index(page_entries)  
            last_idx = self._find_last_paragraph_index(page_entries)

            to_remove = set()

            if first_idx is not None:  
                if self._is_header_footer_candidate(self._plain_text(page_entries[first_idx])):  
                    to_remove.add(first_idx)

            if last_idx is not None and last_idx != first_idx:  
                if self._is_header_footer_candidate(self._plain_text(page_entries[last_idx])):  
                    to_remove.add(last_idx)

            for i, entry in enumerate(page_entries):  
                if i not in to_remove:  
                    cleaned.append(entry)

        return cleaned

    def _remove_global_noise_paragraphs(self, entries):  
        cleaned = []

        for entry in entries:  
            if entry.get("type") == "paragraph":  
                if self._is_global_noise_paragraph(entry.get("content", "")):  
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
        merged = []  
        i = 0

        while i < len(entries):  
            entry = entries[i]

            if entry.get("type") != "paragraph":  
                merged.append(entry)  
                i += 1  
                continue

            current_text = entry.get("content", "")

            if not self._ends_broken(current_text) or self._word_count(current_text) <= 10:  
                merged.append(entry)  
                i += 1  
                continue

            j = i + 1  
            intervening = []

            while j < len(entries) and entries[j].get("type") != "paragraph":  
                intervening.append(entries[j])
                j += 1

            if j < len(entries):  
                next_entry = entries[j]  
                next_text = next_entry.get("content", "")

                if (  
                    self._starts_broken(next_text)  
                    and self._word_count(next_text) > 10  
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
        text = text.rstrip()

        if not text:  
            return False

        if text.endswith("-"):  
            return True

        if text.endswith((".", "!", "?")):  
            return False

        return text[-1].islower()

    def _starts_broken(self, text):  
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
        lower = content.lower()  
        if "<figcaption" in lower:  
            return False  
        return "logo" in lower

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
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return False

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

        if re.match(r"^\d{4},?\s*vol\.?\s*\d+\(?\d+\)?\s+\d+[–-]\d+$", t, flags=re.IGNORECASE):  
            return True

        if re.match(r"^©\s*the author\(s\)\s*\d{4}$", t, flags=re.IGNORECASE):  
            return True

        if "creative commons" in tl and "license" in tl:  
            return True

        if self._is_global_noise_paragraph(t):  
            return True

        return False

    def _is_header_footer_candidate(self, text):  
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return False

        if re.match(r"^\d+\s+global spine journal\s+\d+\(\d+\)$", t, flags=re.IGNORECASE):  
            return True

        if "et al" in tl:  
            return True

        if self._is_global_noise_paragraph(t):  
            return True

        if re.match(r"^\d+\b", t) and len(t.split()) <= 8:  
            return True

        return False

    def _is_global_noise_paragraph(self, text):  
        t = self._strip_md(text).strip()  
        tl = t.lower()

        if not t:  
            return True

        if re.fullmatch(r"[*•·\-_=]{3,}", t):  
            return True

        if "www." in tl or "http://" in tl or "https://" in tl:  
            return True

        if any(domain in tl for domain in [".com", ".org", ".edu", ".cn", ".gov"]):  
            return True

        if "et al" in tl:  
            return True

        if "copyright" in tl:  
            return True

        if "all rights reserved" in tl:  
            return True

        if re.match(r"^©\s*\d{4}\b", t, flags=re.IGNORECASE):  
            return True

        if tl in {"spine", "spine deformity", "global spine journal"}:  
            return True

        return False