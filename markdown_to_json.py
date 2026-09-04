import json  
import re
from pathlib import Path

import utils


class JSONMaker:

    """
    Parse the NuExtract model output markdowns to a single json.
    """

    def __init__(self, output_path):  
        self.output_path = output_path
        self.output_folder = output_path.parent

    def run(self, inputs_folder):  
        inputs_folder = Path(inputs_folder)
        # Since the pdf file was parsed per page md files, gather all the md files.
        md_files = self._get_ordered_md_files(inputs_folder)

        entries = []
        #title of the whole article
        title_found = False

        for page_num, md_file in enumerate(md_files, start=1):  
            text = md_file.read_text(encoding="utf-8")  
            page_entries = self._parse_page(text, page_num)

            if not title_found:  
                for i, entry in enumerate(page_entries):  
                    if entry["type"] == "heading" and entry.get("level") == 1 and self._word_count(entry["content"]) >= 3:
                        entries.append({  
                            "type": "title",  
                            "page": page_num,  
                            "content": entry["content"]
                        })  
                        page_entries.pop(i)  
                        title_found = True  
                        break

            entries.extend(page_entries)

        self.output_folder.mkdir(parents=True, exist_ok=True)

        with self.output_path.open("w", encoding="utf-8") as f:  
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def _get_ordered_md_files(self, folder):  
        md_files = list(folder.glob("*.md"))

        def sort_key(path):  
            nums = re.findall(r"\d+", path.stem)  
            return int(nums[-1]) if nums else float("inf")

        return sorted(md_files, key=sort_key)

    def _parse_page(self, text, page_num):
        """
        Split the .md page into blocks - paragraphs, tables, figures, headings, lists.
        """
        lines = text.splitlines()  
        entries = []  
        current_block = []

        i = 0
        #go line by line
        while i < len(lines):  
            line = lines[i]

            # blank line always ends a block  
            if not line.strip():
                self._flush_block(current_block, page_num, entries)  
                current_block = []  
                i += 1  
                continue

            # html block: keep consuming until closing tag  
            if self._looks_like_html_start(line):  
                self._flush_block(current_block, page_num, entries)  
                current_block = []

                html_block, next_i = self._consume_html_block(lines, i)  
                entry = self._classify_block(html_block, page_num)  
                if entry:  
                    entries.append(entry)
                i = next_i  
                continue

            # if current block exists and this new line starts without indentation,  
            # treat it as a new block  
            if current_block and re.match(r"^\S", line):  
                self._flush_block(current_block, page_num, entries)  
                current_block = [line]  
            else:  
                current_block.append(line)

            i += 1

        self._flush_block(current_block, page_num, entries)  
        return entries  

    def _classify_block(self, block, page_num):  
        lines = [line for line in block.splitlines() if line.strip()]  
        if not lines:  
            return None

        first = lines[0].strip()

        # Headings matched by leading "###"
        m = re.match(r"^(#{1,6})\s+(.*)$", first)  
        if m and len(lines) == 1:  
            return {  
                "type": "heading",  
                "page": page_num,  
                "level": len(m.group(1)),  
                "content": m.group(2)  
            }
        # Figures matched
        if first.lower().startswith("<figure"):  
            return {  
                "type": "figure",  
                "page": page_num,  
                "content": block  
            }
        # Tables matched
        if first.lower().startswith("<table"):  
            return {  
                "type": "table",  
                "page": page_num,  
                "content": block  
            }
        # Other types of html entries
        if self._looks_like_html_start(first):  
            return {  
                "type": "html",  
                "page": page_num,  
                "content": block  
            }

        #if looks like list, we search the next entries to include the following list points
        if all(self._is_list_line(line) for line in lines):  
            return {  
                "type": "list",  
                "page": page_num,  
                "content": block  
            }
        # If none of the above ones were triggere, we assume paragraph
        return {  
            "type": "paragraph",  
            "page": page_num,  
            "content": block  
        }  

    def _is_list_line(self, line):  
        s = line.lstrip()  
        return (  
            re.match(r"^[-+*]\s+.+$", s) is not None or  
            re.match(r"^\d+[.)]\s+.+$", s) is not None
        )

    def _flush_block(self, lines, page_num, entries):
        """
        A bunch of lines have been grouped together a a block. We flush them to the json as a single entry, only thing left to do is classify type.
        """
        if not lines:  
            return

        block = "\n".join(lines).strip()  
        if not block:  
            return

        entry = self._classify_block(block, page_num)  
        if entry:  
            entries.append(entry)

    def _looks_like_html_start(self, line):  
        return re.match(r"^\s*<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", line) is not None

    def _consume_html_block(self, lines, start_idx):
        # since an html tag was opened, we parse through the subsequent lines to consume the whole html structure into this one block
        first_line = lines[start_idx]
        #match the tag of this first html element
        m = re.match(r"^\s*<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", first_line)
        tag = m.group(1).lower() if m else None

        block_lines = [first_line]  
        i = start_idx + 1

        # self-closing or opening+closing (has </>) on same line 
        if re.search(r"/>\s*$", first_line) or (tag and f"</{tag}>" in first_line.lower()):  
            return "\n".join(block_lines), i

        while i < len(lines):  
            block_lines.append(lines[i])  
            if tag and f"</{tag}>" in lines[i].lower():  
                i += 1  
                break  
            i += 1

        return "\n".join(block_lines), i

    def _word_count(self, text):  
        plain = utils._strip_md(text)  
        return len(re.findall(r"\b\w+\b", plain))