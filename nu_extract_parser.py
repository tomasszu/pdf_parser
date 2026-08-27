from openai import OpenAI
import base64

import fitz  # pip install pymupdf

import time

import subprocess

from pathlib import Path


## Create a class wrapper for the opendataloader-pdf library

class NuExtractParser:
    def __init__(self, outputs_path):
        self.outputs_path = outputs_path
        # self.pdfs = pdfs
        self.client = OpenAI(  
            api_key="lm-studio",  
            base_url="http://localhost:1234/v1"
        )

    def parse(self, pdf_dir):
        data_urls = self.pdf_to_png_data_urls(pdf_dir, dpi=170)

        for i, data_url in enumerate(data_urls):

            output_file = f"{self.outputs_path}/{i}.md"

            # 1. Define your path and filename
            output_file = Path(output_file)

            # 2. Create all missing parent folders automatically
            output_file.parent.mkdir(parents=True, exist_ok=True)

            start = time.perf_counter()

            response = self.client.chat.completions.create(
                model="numind/NuExtract3",
                temperature=1,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url}
                            }
                            
                        ],
                    }
                ],
                extra_body={
                    "chat_template_kwargs": {
                        "mode": "markdown",
                        "enable_thinking": False
                    }
                }
            )

            end = time.perf_counter()
            try: 
                print(response.choices[0].message.content)
                response = response.choices[0].message.content

                with open(output_file, "x", encoding="utf-8") as f:
                    f.write(response)
                
                print(f"Elapsed: {(end - start) * 1e3:.3f} ms")
                print(f"Success: Output saved to {output_file}")

            except subprocess.CalledProcessError as e:
                print(f"Error executing command: {e.stderr}")



    def pdf_to_png_data_urls(self, pdf_path, dpi=170):
        data_urls = []

        with fitz.open(pdf_path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                png_bytes = pix.tobytes("png")
                png_base64 = base64.b64encode(png_bytes).decode("utf-8")
                data_urls.append(f"data:image/png;base64,{png_base64}")

        return data_urls
