import logging  
import re  
import time  
from pathlib import Path  
from typing import Optional, Tuple
import fitz  # PyMuPDF
from PIL import Image

from docling_core.types.doc import ImageRefMode, PictureItem, TableItem, TextItem  
from docling.datamodel.base_models import InputFormat  
from docling.datamodel.pipeline_options import PdfPipelineOptions  
from docling.document_converter import DocumentConverter, PdfFormatOption


_log = logging.getLogger(__name__)


class DoclingPdfPageProcessor:  
    def __init__(  
        self,  
        output_dir: str | Path,  
        image_resolution_scale: float = 2.0,  
        generate_page_images: bool = True,
        generate_picture_images: bool = True,  
        logger: Optional[logging.Logger] = None 
    ):  
        self.output_dir = Path(output_dir).resolve()  
        self.image_resolution_scale = image_resolution_scale  
        self.generate_page_images = generate_page_images  
        self.generate_picture_images = generate_picture_images  
        self.logger = logger or _log
        self.doc_converter = self._build_converter()

    def _build_converter(self) -> DocumentConverter:  
        pipeline_options = PdfPipelineOptions()  
        pipeline_options.images_scale = self.image_resolution_scale  
        pipeline_options.generate_page_images = self.generate_page_images  
        pipeline_options.generate_picture_images = self.generate_picture_images

        return DocumentConverter(  
            format_options={  
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }  
        )

    def process_pdf(  
        self,  
        input_pdf_path: str | Path,  
        start_page: Optional[int] = None,  
        end_page: Optional[int] = None,  
        save_json_per_page: bool = False,  
    ) -> None:  
        """  
        Process a PDF one page at a time from start_page to end_page inclusive.  
        """

        input_pdf_path = Path(input_pdf_path).resolve()  
        self.output_dir.mkdir(parents=True, exist_ok=True)

        overall_start = time.time()

        total_pages = self.get_pdf_page_count(input_pdf_path)

        if start_page is None:  
            start_page = 1  
        if end_page is None:  
            end_page = total_pages

        if start_page < 1:  
                    raise ValueError(f"start_page must be >= 1, got {start_page}")  
        if end_page > total_pages:  
            raise ValueError(  
                f"end_page ({end_page}) exceeds total pages in PDF ({total_pages})"  
            )  
        if start_page > end_page:  
            raise ValueError(  
                f"start_page ({start_page}) cannot be greater than end_page ({end_page})"  
            )

        self.logger.info(  
            f"Processing pages {start_page} to {end_page} of {total_pages} total pages."  
        )

        for page_no in range(start_page, end_page + 1):  
            self.logger.info(f"Processing page {page_no}...")  
            try:  
                self.process_single_page(  
                    input_pdf_path=input_pdf_path,  
                    page_no=page_no,  
                    save_json=save_json_per_page,  
                )
            except Exception as e:  
                self.logger.exception(f"Failed to process page {page_no}: {e}")

        elapsed = time.time() - overall_start  
        self.logger.info(f"Finished processing PDF in {elapsed:.2f} seconds.")

    def process_single_page(  
        self,  
        input_pdf_path: str | Path,  
        page_no: int,  
        save_json: bool = False,  
    ) -> None:  
        """  
        Convert and process exactly one page.  
        """  
        input_pdf_path = Path(input_pdf_path).resolve()

        start_time = time.time()

        conv_res = self.doc_converter.convert(  
            str(input_pdf_path),  
            page_range=(page_no, page_no),  
        )

        doc_filename = conv_res.input.file.stem

        table_counter = 0  
        picture_counter = 0  
        prev_element = None

        for element, _level in conv_res.document.iterate_items():  
            if isinstance(element, TableItem):  
                table_counter += 1  
                self._save_table_image(  
                    element=element,  
                    document=conv_res.document,  
                    page_no=page_no,  
                    table_counter=table_counter,  
                    prev_element=prev_element,  
                )

            elif isinstance(element, PictureItem):  
                picture_counter += 1  
                self._save_picture_image(  
                    element=element,  
                    document=conv_res.document,  
                    page_no=page_no,  
                    picture_counter=picture_counter,  
                )

            prev_element = element

        if save_json:  
            json_path = self.output_dir / str(page_no) / f"{doc_filename}-page-{page_no}.json"  
            json_path.parent.mkdir(parents=True, exist_ok=True)  
            conv_res.document.save_as_json(json_path, image_mode=ImageRefMode.REFERENCED)

        elapsed = time.time() - start_time  
        self.logger.info(f"Page {page_no} processed in {elapsed:.2f} seconds.")

    def _save_table_image(  
        self,  
        element: TableItem,  
        document,  
        page_no: int,  
        table_counter: int,  
        prev_element,  
    ) -> None:  
        cap_nr = self._extract_table_caption_number(element, document, prev_element)

        if cap_nr is not None:  
            image_path = self.output_dir / str(page_no) / "tables" / f"{cap_nr}.png"  
        else:  
            image_path = self.output_dir / str(page_no) / "tables" / f"no_cap_{table_counter}.png"

        image_path.parent.mkdir(parents=True, exist_ok=True)  
        with image_path.open("wb") as fp:  
            element.get_image(document).save(fp, "PNG")

    def _save_picture_image(  
        self,  
        element: PictureItem,  
        document,  
        page_no: int,  
        picture_counter: int,  
    ) -> None:  
        cap_nr = self._extract_picture_caption_number(element, document)

        if cap_nr is not None:  
            image_path = self.output_dir / str(page_no) / "images" / f"{cap_nr}.png"  
        else:  
            image_path = self.output_dir / str(page_no) / "images" / f"no_cap_{picture_counter}.png"

        image = element.get_image(document)
        if image is None:
            self.logger.warning(  
                f"Skipping picture on page {page_no}: no image could be generated." 
            )  
            return
        
        w, h = image.size  
        if w < 150 or h < 150:  
            self.logger.warning(  
                f"Skipping picture on page {page_no}: image too small ({w}x{h})."  
            )  
            return

        image_path.parent.mkdir(parents=True, exist_ok=True)  
        with image_path.open("wb") as fp:  
            image.save(fp, "PNG")

    def _extract_picture_caption_number(self, element: PictureItem, document) -> Optional[int]:  
        """  
        Try multiple strategies to find the figure number:  
        1. linked caption  
        2. child text elements  
        """  
        cap = element.caption_text(document)

        # 1. linked caption  
        if cap:  
            cap_text = self._strip_md(cap)  
            cap_nr = self._extract_opendata_figure_number(cap_text)  
            if cap_nr is not None:  
                return cap_nr

        # 2. search in children elements  
        for child in getattr(element, "children", []):  
            ref = getattr(child, "cref", None)  
            if ref is None:  
                continue

            for el in getattr(document, "texts", []):  
                if getattr(el, "self_ref", None) == ref:  
                    text = getattr(el, "text", "")  
                    cap_nr = self._extract_opendata_figure_number(text)  
                    if cap_nr is not None:  
                        return cap_nr

        return None

    def _extract_table_caption_number(self, element: TableItem, document, prev_element) -> Optional[int]:  
        """  
        Try multiple strategies to find the table number:  
        1. linked caption  
        2. table cell content  
        3. preceding text element  
        """  
        cap = element.caption_text(document)

        # 1. linked caption  
        if cap:  
            cap_text = self._strip_md(cap)  
            cap_nr = self._extract_opendata_table_number(cap_text)  
            if cap_nr is not None:  
                return cap_nr

        # 2. search in table cells  
        for cell in element.data.table_cells:  
            text = cell.text  
            cap_nr = self._extract_opendata_table_number(text)  
            if cap_nr is not None:  
                return cap_nr

        # 3. search in preceding element  
        return self._search_for_table_caption_nr(prev_element)

    def _search_for_table_caption_nr(self, preceding_element) -> Optional[int]:  
        if preceding_element and isinstance(preceding_element, TextItem):  
            text = preceding_element.text  
            return self._extract_opendata_table_number(text)  
        return None

    def get_pdf_page_count(self, file_path):
        # Open the document
        doc = fitz.open(file_path)
        
        # Get the page count
        return doc.page_count

    @staticmethod  
    def _extract_opendata_table_number(text: str) -> Optional[int]:  
        text = DoclingPdfPageProcessor._strip_md(text)  
        m = re.search(  
            r"^\s*table\s+(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return int(m.group(1)) if m else None

    @staticmethod  
    def _extract_opendata_figure_number(text: str) -> Optional[int]:  
        text = DoclingPdfPageProcessor._strip_md(text)  
        m = re.search(  
            r"^\s*(?:fig(?:ure)?)\.?\s*[:.]?\s*(\d+)\b",  
            text,  
            flags=re.IGNORECASE,  
        )  
        return int(m.group(1)) if m else None

    @staticmethod  
    def _strip_md(text: str) -> str:  
        text = text.replace("**", "").replace("__", "")  
        text = text.replace("*", "").replace("_", "")  
        text = re.sub(r"\s+", " ", text)  
        return text.strip()