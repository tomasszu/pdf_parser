from parser import PDFParser
from cleanup_json import CleanupJSON

from split_chapters import ChapterSplitter

from chapter_chunker import ChapterChunker

import os, json

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Lopading in the PDF file, parsing into JSON and cleaning the JSON up >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
pdf_name = "The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles"

input_path = f"C:/Users/lenox/tomass/papers/{pdf_name}.pdf"
# Output folder for json + figures + tables
output_dir = f"output/{pdf_name}/"

# parser = PDFParser(outputs_path=output_dir)

# parser.parse(input_path)

# cleanup = CleanupJSON(output_dir=output_dir)

# cleanup.run(json_path=f"{output_dir}/{pdf_name}.json", pdf_path=input_path)

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<Splitting the JSON file into separate files for each chapter and adding token amt to each block>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

# chapsplit = ChapterSplitter(outputs_path=output_dir)

# chapsplit.split(infile=f"{output_dir}/{pdf_name}.json")

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<Splitting the JSON file into separate files for each chapter>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

chunker = ChapterChunker(  
    model_name="Qwen2.5-14B-Instruct",  
    token_limit=350,
    soft_margin=0.10,  
)

for file in os.listdir(f"output/{pdf_name}/chapters"):
    if file.endswith("json"):
        with open(f"output/{pdf_name}/chapters/{file}", "r", encoding="utf-8") as f:
            chapter_json = json.load(f)
        chunker.chunk_and_save(chapter_json, f"output/{pdf_name}/chunks")


