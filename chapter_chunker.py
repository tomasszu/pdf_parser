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
        # New structure first, old structure as fallback  
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
        # New structure first, old structure as fallback  
        if block.get("type") == "heading":  
            return True

        style = self.get_style_name(block)  
        return style.startswith("Heading")

    def is_list_paragraph(self, block: Dict[str, Any]) -> bool:  
        # Keep old behavior, but add conservative fallbacks  
        style = self.get_style_name(block)  
        if style == "List Paragraph":  
            return True

        return block.get("type") in {"list", "list_item"}

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
        """  
        Chunk a single section into multiple chapter-shaped JSON chunks.  
        """  
        allowed_over = int(self.token_limit * (1.0 + self.soft_margin))

        chunks: List[Dict[str, Any]] = []  
        current_blocks: List[Dict[str, Any]] = []  
        current_tokens = 0

        last_non_list_block: Optional[Dict[str, Any]] = None

        def finalize_chunk(chunk_blocks: List[Dict[str, Any]]):  
            if not chunk_blocks:  
                return

            final_blocks = chunk_blocks

            # If the chunk starts with a list paragraph, prepend the last non-list block  
            if final_blocks and self.is_list_paragraph(final_blocks[0]) and last_non_list_block is not None:  
                if final_blocks[0].get("id") != last_non_list_block.get("id"):  
                    final_blocks = [deepcopy(last_non_list_block)] + final_blocks

            chunks.append(self.make_chunk_document(source_doc, section_heading, final_blocks))

        i = 0  
        while i < len(blocks):  
            block = blocks[i]  
            block_tokens = self.get_block_tokens(block)

            # If a heading appears, close current chunk first, then start a new one at the heading  
            if self.is_heading_block(block):  
                if current_blocks:  
                    finalize_chunk(current_blocks)  
                    current_blocks = []  
                    current_tokens = 0

                current_blocks = [block]  
                current_tokens = block_tokens

                if not self.is_list_paragraph(block):  
                    last_non_list_block = block

                i += 1  
                continue

            prospective_tokens = current_tokens + block_tokens

            if current_blocks and prospective_tokens > allowed_over:  
                finalize_chunk(current_blocks)  
                current_blocks = [block]  
                current_tokens = block_tokens  
            else:  
                current_blocks.append(block)  
                current_tokens = prospective_tokens

            if not self.is_list_paragraph(block):  
                last_non_list_block = block

            i += 1

        if current_blocks:  
            finalize_chunk(current_blocks)

        return chunks

    def iter_chunks(self, chapter_json: Dict[str, Any]) -> Iterator[Dict[str, Any]]:  
        """  
        Yield chunks one at a time.  
        """  
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
        """  
        Return all chunks as a list.  
        """  
        return list(self.iter_chunks(chapter_json))

    def chunk_and_save(self, chapter_json: Dict[str, Any], output_dir: str) -> List[Path]:  
        """  
        Chunk a chapter JSON and save all chunks into output_dir.  
        Returns the list of written file paths.  
        """  
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

            outpath = outdir / f"{i:03d}_{heading_name}.json"

            with open(outpath, "w", encoding="utf-8") as f:  
                json.dump(chunk, f, indent=2, ensure_ascii=False)

            written_paths.append(outpath)

        return written_paths  