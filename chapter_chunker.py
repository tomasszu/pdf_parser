import json  
import re  
from copy import deepcopy  
from pathlib import Path  
from typing import Any, Dict, List, Optional, Iterator


class ChapterChunker:  
    def __init__(  
        self,  
        model_name: str,  
        token_limit: int,  
        soft_margin: float = 0.10,  
    ):  
        self.model_name = model_name  
        self.token_limit = token_limit  
        self.soft_margin = soft_margin

    def get_block_tokens(self, block: Dict[str, Any]) -> int:  
        if "tokens" in block:  
            return int(block.get("tokens", {}).get(self.model_name, 0))

        return int(  
            block.get("meta", {})  
                 .get("tokens", {})  
                 .get(self.model_name, 0)
        )

    def get_style_name(self, block: Dict[str, Any]) -> str:  
        return block.get("meta", {}).get("style_name", "")

    def is_heading_block(self, block: Dict[str, Any]) -> bool:  
        return block.get("type") == "heading"

    def get_assigned_heading_level(self, block: Dict[str, Any]) -> Optional[int]:  
        try:  
            return int(block.get("assigned_level"))  
        except (TypeError, ValueError):  
            return None

    def is_list_paragraph(self, block: Dict[str, Any]) -> bool:  
        style = self.get_style_name(block)  
        if style == "List Paragraph":  
            return True

        return block.get("type") in {"list", "list_item"}

    def is_ignorable_block(self, block: Dict[str, Any]) -> bool:  
        return block.get("type") in {"html", "figure", "table"}

    def make_chunk_document(  
        self,  
        source_doc: Dict[str, Any],  
        section_heading: str,  
        blocks: List[Dict[str, Any]],  
    ) -> Dict[str, Any]:  
        document = {  
            "title": source_doc.get("title", ""),  
            "sections": [  
                {  
                    "heading": section_heading,  
                    "kids": blocks  
                }  
            ]  
        }

        if "source" in source_doc:  
            document["source"] = source_doc["source"]

        return {"document": document}

    def _chunk_section_blocks(  
        self,  
        source_doc: Dict[str, Any],  
        section_heading: str,  
        blocks: List[Dict[str, Any]],  
    ) -> List[Dict[str, Any]]:  
        allowed_over = int(self.token_limit * (1.0 + self.soft_margin))

        chunks: List[Dict[str, Any]] = []  
        current_blocks: List[Dict[str, Any]] = []  
        current_tokens = 0

        last_non_list_block: Optional[Dict[str, Any]] = None

        heading_stack: List[Dict[str, Any]] = []  
        heading_stack_levels: List[int] = []  
        chunk_heading_prefix: List[Dict[str, Any]] = []

        def update_heading_stack(block: Dict[str, Any]):  
            assigned_level = self.get_assigned_heading_level(block)  
            if assigned_level is None:  
                return

            while heading_stack_levels and heading_stack_levels[-1] >= assigned_level:  
                heading_stack.pop()  
                heading_stack_levels.pop()

            heading_stack.append(deepcopy(block))  
            heading_stack_levels.append(assigned_level)

        def get_prefix_for_block_start(block: Dict[str, Any]) -> List[Dict[str, Any]]:  
            prefix_stack = deepcopy(heading_stack)  
            prefix_levels = list(heading_stack_levels)

            if self.is_heading_block(block):  
                incoming_level = self.get_assigned_heading_level(block)  
                if incoming_level is not None:  
                    while prefix_levels and prefix_levels[-1] >= incoming_level:  
                        prefix_stack.pop()  
                        prefix_levels.pop()

            return prefix_stack

        def finalize_chunk(chunk_blocks: List[Dict[str, Any]]):  
            if not chunk_blocks:  
                return

            final_blocks = list(chunk_blocks)  
            prefix_blocks: List[Dict[str, Any]] = []

            for idx, hb in enumerate(chunk_heading_prefix):  
                if idx < len(final_blocks):  
                    same_type = final_blocks[idx].get("type") == hb.get("type")  
                    same_content = final_blocks[idx].get("content") == hb.get("content")  
                    if same_type and same_content:  
                        continue  
                prefix_blocks.append(deepcopy(hb))

            final_blocks = prefix_blocks + final_blocks

            if final_blocks and self.is_list_paragraph(final_blocks[0]) and last_non_list_block is not None:  
                duplicate = (  
                    final_blocks[0].get("id") == last_non_list_block.get("id")  
                    if final_blocks[0].get("id") is not None and last_non_list_block.get("id") is not None  
                    else final_blocks[0].get("content") == last_non_list_block.get("content")  
                )  
                if not duplicate:  
                    final_blocks = [deepcopy(last_non_list_block)] + final_blocks

            chunks.append(self.make_chunk_document(source_doc, section_heading, final_blocks))

        i = 0  
        len_b = len(blocks)

        while i < len_b:  
            block = blocks[i]

            pre_block_heading_stack = get_prefix_for_block_start(block)

            if self.is_ignorable_block(block):  
                i += 1  
                continue

            block_tokens = self.get_block_tokens(block)

            if self.is_heading_block(block) and i + 1 < len_b:  
                ni = 1  
                next_block = blocks[i + ni]  
                next_tokens = block_tokens

                while self.is_heading_block(next_block) and i + ni + 1 < len_b:  
                    ni += 1  
                    next_tokens += self.get_block_tokens(next_block)  
                    next_block = blocks[i + ni]

                if current_blocks and current_tokens + next_tokens + self.get_block_tokens(next_block) >= allowed_over:  
                    finalize_chunk(current_blocks)  
                    current_blocks = []  
                    current_tokens = 0

                if not current_blocks:  
                    chunk_heading_prefix = deepcopy(pre_block_heading_stack)

                current_blocks.append(deepcopy(block))  
                current_tokens += block_tokens

                if not self.is_list_paragraph(block):  
                    last_non_list_block = deepcopy(block)

                update_heading_stack(block)

                i += 1  
                continue

            prospective_tokens = current_tokens + block_tokens

            if current_blocks and prospective_tokens > allowed_over:  
                finalize_chunk(current_blocks)  
                current_blocks = []  
                current_tokens = 0

            if not current_blocks:  
                chunk_heading_prefix = deepcopy(pre_block_heading_stack)

            current_blocks.append(deepcopy(block))  
            current_tokens += block_tokens

            if not self.is_list_paragraph(block):  
                last_non_list_block = deepcopy(block)

            if self.is_heading_block(block):  
                update_heading_stack(block)

            i += 1

        if current_blocks:  
            finalize_chunk(current_blocks)

        return chunks

    def iter_chunks(self, chapter_json: Dict[str, Any]) -> Iterator[Dict[str, Any]]:  
        doc = chapter_json["document"]  
        sections = doc.get("sections", [])

        for section in sections:  
            section_heading = section.get("heading", "")  
            blocks = section.get("kids", section.get("blocks", []))

            section_chunks = self._chunk_section_blocks(  
                source_doc=doc,  
                section_heading=section_heading,  
                blocks=blocks,  
            )

            for chunk in section_chunks:  
                yield chunk

    def chunk_chapter(self, chapter_json: Dict[str, Any]) -> List[Dict[str, Any]]:  
        return list(self.iter_chunks(chapter_json))

    def chunk_and_save(self, chapter_json: Dict[str, Any], output_dir: str) -> List[Path]:  
        outdir = Path(output_dir)  
        outdir.mkdir(parents=True, exist_ok=True)

        chunks = self.chunk_chapter(chapter_json)

        written_paths: List[Path] = []

        for i, chunk in enumerate(chunks, 1):  
            section_heading = (  
                chunk.get("document", {})  
                     .get("sections", [{}])[0]  
                     .get("heading", "")  
            )  
            heading_name = re.sub(r"[^A-Za-z0-9_-]+", "_", section_heading).strip("_") or "section"

            outpath = outdir / f"{heading_name}_{i:03d}.json"

            with open(outpath, "w", encoding="utf-8") as f:  
                json.dump(chunk, f, indent=2, ensure_ascii=False)

            written_paths.append(outpath)

        return written_paths