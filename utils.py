import re

def _strip_md(text):  
    text = re.sub(r"!\[.*?\]\(.*?\)", " ", text)  
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  
    text = text.replace("**", "").replace("__", "")  
    text = text.replace("*", "").replace("_", "")  
    text = re.sub(r"\s+", " ", text)  
    return text.strip()

def _extract_opendata_figure_number(text):
    text = _strip_md(text)  
    m = re.search(  
        r"^\s*(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
        text,  
        flags=re.IGNORECASE,  
    )  
    return {  
        "number": int(m.group(1)) if m else None,  
        "caption": text.strip() if text else None,  
    }

def _extract_opendata_table_number(text):  
    text = _strip_md(text)
    m = re.search(  
        r"^\s*table\s+(\d+)\b",  
        text,  
        flags=re.IGNORECASE,  
    )  
    return {  
        "number": int(m.group(1)) if m else None,
        "caption": text.strip() if text else None,
    }