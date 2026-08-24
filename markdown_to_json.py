import json  
import re  
from pathlib import Path


class JSONMaker:  
    def __init__(self, outputs_path):  
        self.outputs_path = Path(outputs_path)

    def run(self, inputs_folder, output_name="combined_blocks.json"):  
        inputs_folder = Path(inputs_folder)  
        md_files = self._get_ordered_md_files(inputs_folder)

        entries = []  
        title_found = False

        for page_num, md_file in enumerate(md_files, start=1):  
            text = md_file.read_text(encoding="utf-8")  
            page_entries = self._parse_page(text, page_num)

            if not title_found:  
                for i, entry in enumerate(page_entries):  
                    if entry["type"] == "heading" and entry.get("level") == 1:  
                        entries.append({  
                            "type": "title",  
                            "page": page_num,  
                            "content": entry["content"]
                        })  
                        page_entries.pop(i)  
                        title_found = True  
                        break

            entries.extend(page_entries)

        self.outputs_path.mkdir(parents=True, exist_ok=True)  
        output_file = self.outputs_path / output_name

        with output_file.open("w", encoding="utf-8") as f:  
            json.dump(entries, f, ensure_ascii=False, indent=2)

        return entries

    def _get_ordered_md_files(self, folder):  
        md_files = list(folder.glob("*.md"))

        def sort_key(path):  
            nums = re.findall(r"\d+", path.stem)  
            return int(nums[-1]) if nums else float("inf")

        return sorted(md_files, key=sort_key)

    def _parse_page(self, text, page_num):
        lines = text.splitlines()  
        entries = []  
        current_block = []

        i = 0  
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

        m = re.match(r"^(#{1,6})\s+(.*)$", first)  
        if m and len(lines) == 1:  
            return {  
                "type": "heading",  
                "page": page_num,  
                "level": len(m.group(1)),  
                "content": m.group(2)  
            }

        if first.lower().startswith("<figure"):  
            return {  
                "type": "figure",  
                "page": page_num,  
                "content": block  
            }

        if first.lower().startswith("<table"):  
            return {  
                "type": "table",  
                "page": page_num,  
                "content": block  
            }

        if self._looks_like_html_start(first):  
            return {  
                "type": "html",  
                "page": page_num,  
                "content": block  
            }

        if all(self._is_list_line(line) for line in lines):  
            return {  
                "type": "list",  
                "page": page_num,  
                "content": block  
            }

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
        first_line = lines[start_idx]  
        m = re.match(r"^\s*<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>", first_line)  
        tag = m.group(1).lower() if m else None

        block_lines = [first_line]  
        i = start_idx + 1

        # self-closing or opening+closing on same line  
        if re.search(r"/>\s*$", first_line) or (tag and f"</{tag}>" in first_line.lower()):  
            return "\n".join(block_lines), i

        while i < len(lines):  
            block_lines.append(lines[i])  
            if tag and f"</{tag}>" in lines[i].lower():  
                i += 1  
                break  
            i += 1

        return "\n".join(block_lines), i  