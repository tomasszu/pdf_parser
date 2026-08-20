import json  
import re  
from pathlib import Path


class JSONMaker:
    def __init__(self, outputs_path):
        self.outputs_path = outputs_path

    def run(self, inputs_folder):
        inputs_folder = Path(inputs_folder)
        md_files = self._get_ordered_md_files(inputs_folder)

        all_entries = []  
        title_found = False

        for page_num, md_file in enumerate(md_files, start=1):  
                    text = md_file.read_text(encoding="utf-8")  
                    page_entries = self._parse_page(text, page_num)

    def _get_ordered_md_files(self, folder):  
        md_files = list(folder.glob("*.md"))

        def sort_key(p):  
            nums = re.findall(r"\d+", p.stem)  
            if nums:  
                return int(nums[-1])  
            return float("inf")

        return sorted(md_files, key=sort_key)

    def _parse_page(self, text, page_num):  
        text = text.replace("\xa0", " ")  
        lines = text.splitlines()

        print(lines)

        return None