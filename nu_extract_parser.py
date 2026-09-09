import base64  
from pathlib import Path  
import re  
import time  
import fitz  # pip install pymupdf  
from openai import OpenAI  
import html  
import unicodedata  
from collections import Counter  
from difflib import SequenceMatcher 




class NuExtractParser:

    def __init__(self, outputs_path):  
        self.outputs_path = outputs_path  
        self.client = OpenAI(  
            api_key="lm-studio", base_url="http://localhost:1234/v1"  
        )

    def base_normalize_text(self, text):  
        text = html.unescape(text)  
        text = text.lower()  
        text = unicodedata.normalize("NFKC", text)

        text = text.replace("\u00ad", "")  
        text = text.replace("–", "-").replace("—", "-").replace("−", "-")  
        text = text.replace("'", "'").replace("\u201c", '"').replace("\u201d", '"')

        # Remove LaTeX BEFORE HTML so that $<0.05$ doesn't look like an HTML tag  
        text = re.sub(r"\$[^$]*\$", " ", text)          # Remove inline LaTeX entirely  
        text = re.sub(r"\\[a-zA-Z]+", " ", text)  
        text = text.replace("$", " ")                     # Catch any remaining $

        # Now safe to remove HTML/XML tags  
        #text = re.sub(r"<img\b[^>]*>", " ", text)
        # Replace <img> tags with their alt text (instead of discarding)  
        text = re.sub(r'<img\b[^>]*\balt="([^"]*)"[^>]*>', r' \1 ', text)
        text = re.sub(r"<[^>]+>", " ", text)

        return text


    def strip_boilerplate_lines(self, text):  
        """  
        Remove common front-matter / publisher lines that are noisy and  
        inconsistently represented between PDF text layer and LLM output.  
        """  
        lines = [line.strip() for line in text.splitlines()]  
        cleaned = []

        for line in lines:  
            low = line.lower()

            if not line:  
                cleaned.append(line)  
                continue

            # DOI line  
            if low.startswith("https://doi.org/"):  
                continue

            # Springer/footer/logo-ish lines  
            if low == "springer":  
                continue

            # Extended author info line  
            if "extended author information available on the last page of the article" in low:  
                continue

            # Received/Revised/Accepted line  
            if low.startswith("received:") or "published online:" in low:  
                continue

            # Copyright line  
            if low.startswith("© the author"):  
                continue

            cleaned.append(line)

        return "\n".join(cleaned)


    def normalize_pdf_text(self, text):  
        text = self.base_normalize_text(text)  
        text = self.strip_boilerplate_lines(text)

        # Safe dehyphenation only:  
        # multi-\ncentric -> multicentric  
        text = re.sub(r"([a-z]{2,})-\s*\n\s*([a-z]{2,})", r"\1\2", text)

        # Collapse whitespace  
        text = re.sub(r"\s+", " ", text)

        # Split hyphenated compounds into words  
        text = text.replace("-", " ")

        return text


    def normalize_llm_text(self, text):  
        text = self.base_normalize_text(text)  
        text = self.strip_boilerplate_lines(text)

        # Collapse whitespace  
        text = re.sub(r"\s+", " ", text)

        # Split hyphenated compounds into words  
        text = text.replace("-", " ")

        return text


    def extract_words(self, text, min_len=6):  
        return re.findall(rf"\b[a-z]{{{min_len},}}\b", text)

    def verify_extraction(self, digital_text, llm_text, threshold=0.85, debug=False):  
        """  
        Main pass/fail metric = unique-word recall.

        Also computes count recall for diagnostics only.

        Returns:  
            (is_valid, unique_recall)  
        """

        if not digital_text.strip():  
            print("[WARN] The PDF page has no digital text; skipping verification.")  
            return True, 1.0

        pdf_text = self.normalize_pdf_text(digital_text)  
        llm_text = self.normalize_llm_text(llm_text)

        pdf_words = self.extract_words(pdf_text, min_len=6)  
        llm_words = self.extract_words(llm_text, min_len=6)

        pdf_unique = set(pdf_words)  
        llm_unique = set(llm_words)

        # probe_words = [  
        #     "angles", "available", "baseline", "different", "included",  
        #     "interbody", "interbodies", "located", "lovecchio", "definition"  
        # ]

        # print("\n--- NORMALIZED LLM TEXT ---")  
        # print(llm_text)

        # print("\n--- LLM WORDS SAMPLE ---")  
        # print(llm_words)

        # for w in probe_words:  
        #     print(  
        #         f"{w}: pdf={w in pdf_unique}, llm={w in llm_unique}, "  
        #         f"pdf_count={pdf_words.count(w)}, llm_count={llm_words.count(w)}"  
        #     )

        if not pdf_words:  
            return True, 1.0

        # Primary metric: unique-word recall  
        pdf_unique = set(pdf_words)  
        llm_unique = set(llm_words)

        matched_unique = pdf_unique.intersection(llm_unique)  
        unique_recall = len(matched_unique) / len(pdf_unique) if pdf_unique else 1.0

        # Secondary metric: count recall (debug only)  
        pdf_counts = Counter(pdf_words)  
        llm_counts = Counter(llm_words)

        matched_count = sum(min(pdf_counts[w], llm_counts[w]) for w in pdf_counts)  
        total_count = sum(pdf_counts.values())  
        count_recall = matched_count / total_count if total_count else 1.0

        missing_unique = sorted(pdf_unique - llm_unique)

        if debug:  
            print(f"PDF unique words: {len(pdf_unique)}")  
            print(f"LLM unique words: {len(llm_unique)}")  
            print(f"Matched unique words: {len(matched_unique)}")  
            print(f"Unique recall: {unique_recall:.2%}")  
            print(f"Count recall (debug only): {count_recall:.2%}")  
            print(f"Missing unique words (sample): {missing_unique[:30]}")

        is_valid = unique_recall >= threshold  
        return is_valid, unique_recall  

    def parse(self, pdf_dir, max_retries=3, threshold=0.85):
        print("[INFO] Beginning NuExtract markdown extraction from PDF") 
        # Now returns both the image data URL AND the raw digital text  
        pages_data = self.pdf_to_png_and_text(pdf_dir, dpi=170)

        for i, (data_url, digital_text) in enumerate(pages_data):
            output_file = Path(f"{self.outputs_path}/{i}.md")  
            output_file.parent.mkdir(parents=True, exist_ok=True)

            #print(digital_text)

            attempt = 0  
            success = False  
            content = ""

            while attempt < max_retries and not success:  
                attempt += 1  
                print(  
                    f"\n--- Processing page {i} (Attempt {attempt}/{max_retries}) ---"  
                )  
                start = time.perf_counter()

                try:  
                    response = self.client.chat.completions.create(  
                        model="numind/NuExtract3",  
                        temperature=1.0,
                        messages=[  
                            {  
                                "role": "user",  
                                "content": [  
                                    {  
                                        "type": "image_url",  
                                        "image_url": {"url": data_url},  
                                    }  
                                ],  
                            }  
                        ],  
                        extra_body={  
                            "chat_template_kwargs": {  
                                "mode": "markdown",  
                                "enable_thinking": False,  
                            }  
                        },  
                    )

                    content = response.choices[0].message.content  
                    end = time.perf_counter()

                    # --- VERIFICATION STEP ---  
                    is_valid, token_recall = self.verify_extraction(  
                        digital_text, content, threshold=threshold, debug=True  
                    )

                    print(  
                        f"Page {i} Verification Score: {token_recall:.2%} (Required: {threshold:.0%})"  
                    )

                    if is_valid:  
                        success = True  
                        with open(output_file, "w", encoding="utf-8") as f:  
                            f.write(content)  
                        print(f"Elapsed: {(end - start) * 1e3:.3f} ms")  
                        print(f"Success: Output saved to {output_file}")  
                    else:  
                        print(  
                            f"Warning: Page {i} failed verification "  
                            f"(Score: {token_recall:.2%}). Retrying..."
                        )

                except Exception as e:  
                    print(f"Error on page {i}, attempt {attempt}: {e}")  
                    time.sleep(1)  # Brief pause before retry

            if not success:  
                print(  
                    f"ERROR: Failed to successfully parse page {i} after {max_retries} attempts."
                )  
                # Optional: Write the failed attempt anyway as a fallback  
                if content:  
                    with open(  
                        output_file.with_suffix(".failed.md"),  
                        "w",  
                        encoding="utf-8",  
                    ) as f:  
                        f.write(content)

    def pdf_to_png_and_text(self, pdf_path, dpi=170):  
        """Renders pages to PNG and extracts raw digital text simultaneously."""  
        pages_data = []

        with fitz.open(pdf_path) as doc:  
            for page in doc:  
                # 1. Render to image  
                pix = page.get_pixmap(dpi=dpi, alpha=False)  
                png_bytes = pix.tobytes("png")  
                png_base64 = base64.b64encode(png_bytes).decode("utf-8")  
                data_url = f"data:image/png;base64,{png_base64}"

                # 2. Extract plain digital text  
                digital_text = page.get_text("text")

                pages_data.append((data_url, digital_text))

        return pages_data  