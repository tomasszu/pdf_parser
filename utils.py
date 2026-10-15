import re  
import json  
import logging  
from pathlib import Path

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------- #  
# Markdown / text cleaning  
# --------------------------------------------------------------------- #

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


def normalize(text: str) -> str:  
    """Strip markdown + punctuation + casing so heading text can be compared  
    across NuExtract markdown and Docling parses without superficial  
    mismatches (bold markers, stray punctuation, casing, etc.)."""  
    text = _strip_md(text or "")  
    text = text.strip().lower()  
    text = re.sub(r"[^a-z0-9]+", " ", text)  
    return text.strip()


# --------------------------------------------------------------------- #  
# JSON IO  
# --------------------------------------------------------------------- #

def load_json(path):  
    with open(Path(path), "r", encoding="utf-8") as f:  
        return json.load(f)


def save_json(data, path, indent=2):  
    path = Path(path)  
    path.parent.mkdir(parents=True, exist_ok=True)  
    with open(path, "w", encoding="utf-8") as f:  
        json.dump(data, f, indent=indent, ensure_ascii=False)  
    return path


# --------------------------------------------------------------------- #  
# File discovery  
# --------------------------------------------------------------------- #

def find_file_by_keyword(directory, keyword, suffix=".json"):  
    """Finds a file within `directory` whose stem contains `keyword`  
    (case-insensitive). E.g. locate chapters/02_methods.json given just  
    "methods". Returns None if nothing matches; warns if more than one does."""  
    directory = Path(directory)  
    keyword = keyword.lower()  
    matches = sorted(p for p in directory.glob(f"*{suffix}") if keyword in p.stem.lower())  
    if not matches:  
        return None  
    if len(matches) > 1:  
        _log.warning(  
            "Multiple files matched keyword %r in %s: %s. Using first.",  
            keyword, directory, [m.name for m in matches],  
        )  
    return matches[0]


def docling_page_path(pages_dir, page_no):  
    """Docling pages are saved as pages_dir/<page_no>/page_<page_no>.json"""  
    return Path(pages_dir) / str(page_no) / f"page_{page_no}.json"


def load_docling_page(pages_dir, page_no):  
    path = docling_page_path(pages_dir, page_no)  
    if not path.exists():  
        raise FileNotFoundError(f"Docling page file not found for page {page_no}: {path}")  
    return load_json(path)


# --------------------------------------------------------------------- #  
# Geometry  
# --------------------------------------------------------------------- #

def bbox_dimensions(bbox):  
    """Given a docling-style bbox dict ({'l','t','r','b', ...}), returns  
    {'height', 'width'} rounded to 3 decimals, or None if incomplete."""  
    if not bbox or not all(k in bbox for k in ("t", "b", "l", "r")):  
        return None  
    return {  
        "height": round(bbox["t"] - bbox["b"], 3),  
        "width": round(bbox["r"] - bbox["l"], 3),  
    }


def iter_docling_page_files(pages_dir):  
    """  
    Yields (page_no, path) for every docling page json file under  
    pages_dir/<page_no>/page_<page_no>.json, sorted by page number.  
    """  
    pages_dir = Path(pages_dir)  
    entries = []  
    for subdir in pages_dir.iterdir():  
        if not subdir.is_dir():  
            continue  
        try:  
            page_no = int(subdir.name)  
        except ValueError:  
            continue  
        path = subdir / f"page_{page_no}.json"  
        if path.exists():  
            entries.append((page_no, path))  
    return sorted(entries, key=lambda x: x[0])