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

        # Remove soft hyphens  
        text = text.replace("\u00ad", "")

        # Normalize dash variants  
        text = text.replace("–", "-").replace("—", "-").replace("−", "-")

        # Normalize quotes  
        text = text.replace("’", "'").replace("“", '"').replace("”", '"')

        # Remove LaTeX commands / wrappers  
        text = re.sub(r"\\[a-zA-Z]+", " ", text)  
        text = text.replace("$", " ")

        # Remove image tags entirely  
        text = re.sub(r"<img\b[^>]*>", " ", text)

        # Remove remaining HTML tags but keep content  
        text = re.sub(r"<[^>]+>", " ", text)

        return text


    def normalize_pdf_text_for_tokens(self, text):  
        text = self.base_normalize_text(text)

        # Safe only: fix hyphenated line wraps  
        # e.g. multi-\ncentric -> multicentric  
        text = re.sub(r"([a-z]{2,})-\s*\n\s*([a-z]{2,})", r"\1\2", text)

        # Replace remaining newlines with spaces  
        text = re.sub(r"\s+", " ", text)

        # Split hyphenated compounds into words for matching  
        text = text.replace("-", " ")

        return text


    def normalize_llm_text_for_tokens(self, text):  
        text = self.base_normalize_text(text)

        # Markdown already has sane paragraph structure  
        text = re.sub(r"\s+", " ", text)  
        text = text.replace("-", " ")

        return text


    def extract_pdf_tokens(self, text):  
        text = self.normalize_pdf_text_for_tokens(text)  
        return re.findall(r"\b[a-z]{6,}\b", text)


    def extract_llm_tokens(self, text):  
        text = self.normalize_llm_text_for_tokens(text)  
        return re.findall(r"\b[a-z]{6,}\b", text)


    def normalize_pdf_text_for_chars(self, text):  
        text = self.base_normalize_text(text)

        # Only fix hyphenated wraps  
        text = re.sub(r"([a-z]{2,})-\s*\n\s*([a-z]{2,})", r"\1\2", text)

        # IMPORTANT:  
        # Don't do any arbitrary newline-joining.  
        # Just drop non-letters; linebreaks disappear naturally.  
        text = re.sub(r"[^a-z]+", "", text)  
        return text


    def normalize_llm_text_for_chars(self, text):  
        text = self.base_normalize_text(text)  
        text = re.sub(r"[^a-z]+", "", text)  
        return text  

    def verify_extraction(  
        self,  
        digital_text,  
        llm_text,  
        token_threshold=0.85,  
        char_threshold=0.95,  
        debug=False,  
    ):  
        print("\n\n\n Digitized text:")
        print(digital_text)
        print("\n\n\n LLM text:")
        print(llm_text)
        """  
        Returns:  
            (is_valid, token_recall, char_similarity)  
        """

        if not digital_text.strip():  
            print("[WARN] The PDF page has no digital text; skipping verification.")  
            return True, 1.0, 1.0

        pdf_tokens = self.extract_pdf_tokens(digital_text)  
        llm_tokens = self.extract_llm_tokens(llm_text)

        if not pdf_tokens:  
            return True, 1.0, 1.0

        # Count-based token recall  
        pdf_counts = Counter(pdf_tokens)  
        llm_counts = Counter(llm_tokens)

        matched_count = sum(min(pdf_counts[word], llm_counts[word]) for word in pdf_counts)  
        total_count = sum(pdf_counts.values())  
        token_recall = matched_count / total_count if total_count else 1.0

        # Character-level similarity  
        pdf_chars = self.normalize_pdf_text_for_chars(digital_text)  
        llm_chars = self.normalize_llm_text_for_chars(llm_text)  
        char_similarity = SequenceMatcher(None, pdf_chars, llm_chars).ratio() if pdf_chars else 1.0

        # Missing words debug  
        missing = []  
        for word, count in pdf_counts.items():  
            deficit = count - llm_counts.get(word, 0)  
            if deficit > 0:  
                missing.append((word, deficit))  
        missing.sort(key=lambda x: x[1], reverse=True)

        if debug:  
            print(f"PDF total words: {total_count}")  
            print(f"LLM total words: {len(llm_tokens)}")  
            print(f"Matched word count: {matched_count}")  
            print(f"Token recall: {token_recall:.2%}")  
            print(f"Char similarity: {char_similarity:.2%}")  
            print(f"Top missing words: {missing[:20]}")

        is_valid = (token_recall >= token_threshold) or (char_similarity >= char_threshold)

        return is_valid, token_recall, char_similarity  

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
                    is_valid, token_recall, char_similarity = self.verify_extraction(  
                        digital_text,  
                        content,  
                        token_threshold=threshold,  
                        char_threshold=0.95,  
                        debug=True,  
                    )

                    print(  
                        f"Page {i} Verification: token={token_recall:.2%}, char={char_similarity:.2%} "  
                        f"(Required: token>={threshold:.0%} or char>=95%)"  
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
                            f"(token={token_recall:.2%}, char={char_similarity:.2%}). Retrying..."  
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