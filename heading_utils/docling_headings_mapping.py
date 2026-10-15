"""  
Extracts all section_header nodes from a Docling-parsed document's pages,  
returning each as a dict with its text, page number, and bbox object.  
"""

from utils import iter_docling_page_files, load_json

SECTION_HEADER_LABEL = "section_header"


def map_headings(pages_dir):  
    """  
    Walks every Docling-parsed page under `pages_dir` and collects all  
    section_header nodes into a list of dicts, in page order and  
    top-to-bottom order within each page.

    Returns:  
        List[dict]: each dict shaped as:  
            {  
                "text": str,  
                "page": int,  
                "bbox": dict,  
            }  
    """  
    headings = []

    for page_no, path in iter_docling_page_files(pages_dir):  
        docling_page = load_json(path)

        headers = [  
            node for node in docling_page.get("texts", [])  
            if node.get("label") == SECTION_HEADER_LABEL  
        ]

        # Sort top-to-bottom on the page (BOTTOMLEFT origin -> higher "t" = higher on page)  
        def top_coord(node):  
            prov = (node.get("prov") or [{}])[0]  
            return -(prov.get("bbox", {}).get("t", 0))

        headers.sort(key=top_coord)

        for node in headers:  
            prov = (node.get("prov") or [{}])[0]  
            bbox = prov.get("bbox")

            headings.append({  
                "text": node.get("text", ""),
                "page": page_no,  
                "bbox": bbox,  
            })

    return headings