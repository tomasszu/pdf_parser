"""  
Given a cropped chapter PDF and an anchor heading's text, asks NuExtract  
(vision mode) to identify every other heading in the PDF that shares the  
anchor heading's heading level -- e.g. given "Patient Sample", find  
"Surgical Technique", "Statistical Analysis", etc. if they're siblings at  
the same level.

This is a second-pass, vision-model-based sanity check layered on top of  
the page-layout-based heading-level assignment done elsewhere (weak-  
heading promotion, bbox-based level diffing in HeadingsAdjuster, etc.) --  
NuExtract is shown the actual rendered pages and asked to reason about  
heading level directly off of appearance/semantics, independent of our own  
layout heuristics.

Saves its raw JSON response as an intermediary output, in the same  
directory as the source PDF, named "<pdf-stem>.same_level_headings.json".  
"""

import base64  
import json  
import logging  
from pathlib import Path

import fitz  # pip install pymupdf  
from openai import OpenAI

_log = logging.getLogger(__name__)

TEMPLATE = {  
    "same_level_headings": ["string"]  
}


class SameLevelHeadingsExtractor:

    def __init__(  
        self,  
        model="numind/NuExtract3",  
        base_url="http://localhost:1234/v1",  
        api_key="lm-studio",  
        temperature=0.2,  
        dpi=170,  
        max_retries=3,  
    ):  
        self.model = model  
        self.temperature = temperature  
        self.dpi = dpi  
        self.max_retries = max_retries  
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    # ------------------------------------------------------------------ #  
    def _pdf_to_data_urls(self, pdf_path):  
        data_urls = []  
        with fitz.open(str(pdf_path)) as doc:  
            for page in doc:  
                pix = page.get_pixmap(dpi=self.dpi, alpha=False)  
                png_bytes = pix.tobytes("png")  
                png_base64 = base64.b64encode(png_bytes).decode("utf-8")  
                data_urls.append(f"data:image/png;base64,{png_base64}")  
        return data_urls

    # ------------------------------------------------------------------ #  
    @staticmethod  
    def _build_system_prompt(anchor_heading_text):  
        return (  
            f"Extract the other headings from the provided text that have the same heading "  
            f"level as {anchor_heading_text} in this format:\n\n"  
            "{\n"  
            '  "same_level_headings": [\n'  
            '      "verbatim string of heading",\n'  
            "  ]\n"  
            "}\n\n"  
        )

    # ------------------------------------------------------------------ #  
    def extract(self, pdf_path, anchor_heading_text, outpath=None):  
        """  
        Runs the same-level-heading extraction over every page of  
        `pdf_path`, using `anchor_heading_text` as the reference heading  
        whose level peers should be found.

        Saves the parsed JSON response to `outpath` if given, otherwise to  
        a sibling file next to `pdf_path` named  
        "<pdf-stem>.same_level_headings.json".

        Returns the parsed response dict, or None if all attempts failed  
        (in which case the raw last response, if any, is saved to  
        "<outpath-stem>.failed.json" for inspection).  
        """  
        pdf_path = Path(pdf_path)  
        data_urls = self._pdf_to_data_urls(pdf_path)

        if outpath is None:  
            outpath = pdf_path.with_name(f"{pdf_path.stem}.same_level_headings.json")  
        outpath = Path(outpath)  
        outpath.parent.mkdir(parents=True, exist_ok=True)

        system_prompt = self._build_system_prompt(anchor_heading_text)

        attempt = 0  
        parsed = None  
        content = ""

        while attempt < self.max_retries and parsed is None:  
            attempt += 1  
            _log.info(  
                "Requesting same-level headings for '%s' from %s (attempt %d/%d)",  
                anchor_heading_text, pdf_path.name, attempt, self.max_retries,  
            )

            try:  
                response = self.client.chat.completions.create(  
                    model=self.model,  
                    temperature=self.temperature,  
                    messages=[  
                        {"role": "system", "content": system_prompt},  
                        {  
                            "role": "user",  
                            "content": [  
                                {"type": "image_url", "image_url": {"url": data_url}}  
                                for data_url in data_urls  
                            ],  
                        },  
                    ],  
                    extra_body={  
                        "chat_template_kwargs": {  
                            "template": json.dumps(TEMPLATE, indent=2),  
                            "enable_thinking": False,  
                            "mode": "json",  
                        }  
                    },  
                )

                content = response.choices[0].message.content  
                parsed = json.loads(content)

            except Exception:  
                _log.exception(  
                    "Error extracting same-level headings for '%s' (attempt %d/%d)",  
                    anchor_heading_text, attempt, self.max_retries,  
                )

        if parsed is None:  
            _log.error(  
                "Failed to extract same-level headings for '%s' from %s after %d attempts",  
                anchor_heading_text, pdf_path.name, self.max_retries,  
            )  
            if content:  
                failed_path = outpath.with_suffix(".failed.json")  
                with open(failed_path, "w", encoding="utf-8") as f:  
                    f.write(content)  
                _log.info("Wrote raw failed response to %s", failed_path)  
            return None

        with open(outpath, "w", encoding="utf-8") as f:  
            json.dump(parsed, f, indent=2, ensure_ascii=False)  
        _log.info("Saved same-level headings extraction to %s", outpath)

        return parsed  