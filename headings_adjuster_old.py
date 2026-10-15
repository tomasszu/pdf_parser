import json  
import re  
from copy import deepcopy  
from pathlib import Path  
from typing import Any, Dict, List, Optional

from utils import normalize


class HeadingsAdjuster:  
    def __init__(  
        self,  
        max_assigned_level: int = 5,  
        skip_section_heading_duplicates: bool = True,  
        reset_on_new_page: bool = True,  
    ):  
        self.max_assigned_level = max_assigned_level  
        self.skip_section_heading_duplicates = skip_section_heading_duplicates  
        self.reset_on_new_page = reset_on_new_page

    def _extract_bold_lead(self, text: str) -> Optional[str]:  
        m = re.match(r"^\s*\*\*(.+?)\*\*", text or "")  
        return m.group(1).strip() if m else None

    def is_weak_heading(self, block: Dict[str, Any]) -> bool:  
        return (  
            block.get("type") == "paragraph"  
            and bool(self._extract_bold_lead(block.get("content", "")))  
        )

    def is_heading_block(self, block: Dict[str, Any]) -> bool:  
        return block.get("type") == "heading" or self.is_weak_heading(block)

    def get_verbatim_heading_level(self, block: Dict[str, Any]) -> Optional[int]:
        if block.get("type") != "heading":  
            return None  
        try:  
            return int(block.get("level"))  
        except (TypeError, ValueError):  
            return None

    def materialize_heading_block(self, block: Dict[str, Any], assigned_level: int) -> Dict[str, Any]:  
        out = deepcopy(block)  
        original_type = block.get("type")

        out["type"] = "heading"  
        out["assigned_level"] = assigned_level  
        out["reported_level"] = block.get("level") if original_type == "heading" else None

        out.pop("level", None)

        if original_type != "heading":  
            out["converted_from"] = original_type

        return out

    def _promote_verbatim_level(self, level: Optional[int]) -> Optional[int]:  
        """Raw/verbatim heading level 1 is reserved for the literal chapter/  
        section-title anchor. Any other heading reported as level 1 should be  
        treated as level 2 for all downstream level-diffing, so it stays  
        consistent with headings anchored via the 'first heading on a page'  
        path. Must be applied identically in both the anchoring branch and the  
        already-anchored diff branch, or their scales diverge."""  
        if level == 1:  
            return 2  
        return level

    def adjust_section_blocks(  
        self,  
        section_heading: str,  
        blocks: List[Dict[str, Any]],  
    ) -> List[Dict[str, Any]]:  
        chapter_norm = normalize(section_heading)  
        adjusted_blocks: List[Dict[str, Any]] = []

        anchored = False  
        curr_head_lev = 1  
        verbatim_head_lev: Optional[int] = None  
        current_page: Optional[int] = None
        last_heading_was_weak = False  
        last_assigned_heading_level: Optional[int] = None

        def reset_page_state():  
            nonlocal anchored, curr_head_lev, verbatim_head_lev  
            nonlocal last_heading_was_weak, last_assigned_heading_level

            anchored = False  
            verbatim_head_lev = None

            if last_heading_was_weak and last_assigned_heading_level is not None:  
                curr_head_lev = last_assigned_heading_level  
            else:  
                curr_head_lev = 1  
                last_heading_was_weak = False

        def assign_heading_level(block: Dict[str, Any]) -> Optional[int]:    
            nonlocal anchored, curr_head_lev, verbatim_head_lev    
            nonlocal last_heading_was_weak, last_assigned_heading_level

            if not self.is_heading_block(block):    
                return None  

            # Weak heading    
            if block.get("type") != "heading":    
                if last_heading_was_weak:    
                    assigned = curr_head_lev    
                else:    
                    # FIX (Bug 1): use the persistent last_assigned_heading_level,  
                    # not curr_head_lev, since curr_head_lev may have just been  
                    # wiped to 1 by a page reset even though the real heading  
                    # context (e.g. level 2) should still apply.  
                    base = (  
                        last_assigned_heading_level  
                        if last_assigned_heading_level is not None  
                        else curr_head_lev  
                    )    
                    assigned = min(base + 1, self.max_assigned_level)  

                curr_head_lev = assigned    
                last_heading_was_weak = True    
                last_assigned_heading_level = assigned    
                return assigned  

            incoming_verbatim = self.get_verbatim_heading_level(block)    
            content_norm = normalize(block.get("content", ""))  

            if chapter_norm and content_norm == chapter_norm:    
                anchored = True    
                curr_head_lev = 1    
                verbatim_head_lev = incoming_verbatim    
                last_heading_was_weak = False    
                last_assigned_heading_level = 1    
                return 1  

            if not anchored:    
                # FIX (Bug 2): promote raw level-1 the same way the already-anchored  
                # branch does, and store the PROMOTED value as verbatim_head_lev so  
                # the next heading on this page diffs against the same scale.  
                normalized_incoming = self._promote_verbatim_level(incoming_verbatim)    
                assigned = max(normalized_incoming, 2) if normalized_incoming is not None else 2    
                anchored = True    
                curr_head_lev = assigned    
                verbatim_head_lev = normalized_incoming    
                last_heading_was_weak = False    
                last_assigned_heading_level = assigned    
                return assigned  

            if incoming_verbatim is None or verbatim_head_lev is None:    
                assigned = min(max(curr_head_lev, 2), self.max_assigned_level)    
                curr_head_lev = assigned    
                last_heading_was_weak = False    
                last_assigned_heading_level = assigned    
                return assigned  

            incoming_verbatim = self._promote_verbatim_level(incoming_verbatim)  

            diff = incoming_verbatim - verbatim_head_lev    
            assigned = curr_head_lev + diff    
            assigned = max(2, min(self.max_assigned_level, assigned))  

            curr_head_lev = assigned    
            verbatim_head_lev = incoming_verbatim    
            last_heading_was_weak = False    
            last_assigned_heading_level = assigned    
            return assigned  

        for block in blocks:  
            block_page = block.get("page")

            if self.reset_on_new_page:  
                if current_page is None:  
                    current_page = block_page  
                elif block_page != current_page:  
                    reset_page_state()  
                    current_page = block_page

            if (  
                self.skip_section_heading_duplicates  
                and block.get("type") == "heading"  
                and normalize(block.get("content", "")) == chapter_norm  
            ):  
                assign_heading_level(block)  
                continue

            assigned_level = assign_heading_level(block)

            if assigned_level is not None:  
                adjusted_blocks.append(self.materialize_heading_block(block, assigned_level))  
            else:  
                adjusted_blocks.append(deepcopy(block))

        return adjusted_blocks

    def adjust_chapter(self, chapter_json: Dict[str, Any]) -> Dict[str, Any]:  
        out = deepcopy(chapter_json)  
        doc = out.get("document", {})  
        sections = doc.get("sections", [])

        for section in sections:  
            section_heading = section.get("heading", "")  
            blocks = section.get("kids", section.get("blocks", []))  
            adjusted_blocks = self.adjust_section_blocks(section_heading, blocks)

            if "kids" in section:  
                section["kids"] = adjusted_blocks  
            else:  
                section["blocks"] = adjusted_blocks

        return out

    def adjust_and_save(self, chapter_json: Dict[str, Any], outpath: str) -> Path:  
        adjusted = self.adjust_chapter(chapter_json)  
        outpath = Path(outpath)  
        outpath.parent.mkdir(parents=True, exist_ok=True)

        with open(outpath, "w", encoding="utf-8") as f:  
            json.dump(adjusted, f, indent=2, ensure_ascii=False)

        return outpath