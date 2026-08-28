from parser import OpenDataParser
from nu_extract_parser import NuExtractParser

from markdown_to_json import JSONMaker

from json_post_process import JSONPostProcessor

from augment_json import AugmentJSON

from split_chapters import ChapterSplitter

from chapter_chunker import ChapterChunker

import os, json

from pathlib import Path

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Lopading in the PDF file, parsing into markdown then to JSON and cleaning the JSON up >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
#pdf_name = "The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles"
#pdf_name = "pelvic_nonresponse_following_treatment_of_adult.7"
pdf_name = "Lower_Limb_Khalife"

input_pdf_dir = f"C:/Users/lenox/tomass/papers/{pdf_name}.pdf"
# Output folder for json + figures + tables
output_parent_dir = f"output/{pdf_name}"

"""
 1. Extracting the text from the PDF to markdown via the NuExtract model. You need to have LM studio running and model loaded for this
"""
markdown_dir = f"{output_parent_dir}/markdown"

# parser = NuExtractParser(outputs_path=markdown_dir)

# parser.parse(pdf_dir=input_pdf_dir)

"""
 2. Parse the model output markdowns to json blocks.
"""

jsons_dir = f"{output_parent_dir}/json"

json_file_path = Path(f"{jsons_dir}/combined_blocks.json")

# json_maker = JSONMaker(output_path=json_file_path)

# json_maker.run(inputs_folder=markdown_dir)

"""
 3. Aims to relieve 3 things: a) header and footer noise, b) logo images noise, c) paragraph fragmentation in case of page break or structural break.
"""

cleaned_json_path = Path(f"{jsons_dir}/combined_blocks_clean.json")

# postprocessor = JSONPostProcessor(output_path=cleaned_json_path)

# postprocessor.run(input_json=json_file_path)

"""
<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< parse separately with Opendataparser to extract images of figures and tables>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
"""

"""
 1. Extracting the content (with the primary aim to get figure crops and table bbox coordinates) from the PDF to json via the OpenData PDFParser model.
 You need to have installed: pip install -U "opendataloader-pdf[hybrid]"
 You need to launch: opendataloader-pdf-hybrid --port 5002
"""

opendata_outputs_dir = f"{output_parent_dir}/opendata_parser"

opendata_json_path = Path(f"{opendata_outputs_dir}/{pdf_name}.json")

# opendata_parser = OpenDataParser(outputs_path=opendata_outputs_dir)

# opendata_parser.parse(input_pdf_dir, output_format="json, pdf")

"""
 2. The following JSON Augmentation deals with two separate issues:
    a.) The Opendata json is messy - harder to read: artifacts are being read off of images and saved as text entries, figures are not always psoitioned next to their captions, tables are imperfect, hence need to be replaced with cropped images of them from the pdf.
    b.) The NuExtract json lacks actual images linked to the figures and tables are also imperfect, hence better replaced for images.

    So the augumentation a) cleans up the opendata json and replaced the tables w cropped images and b) replaces the figures and tables entries in the NuExtract json with images gained from opendata json.

"""
augmented_opendata_json = opendata_json_path.parent / f"{pdf_name}_augmented.json"

# augment = AugmentJSON(output_dir=opendata_outputs_dir, output_file=augmented_opendata_json)

# augment.run(nuext_input_json=cleaned_json_path, opendata_input_json=opendata_json_path, pdf_path=input_pdf_dir)

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<Splitting the JSON file into separate files for each chapter and adding token amt to each block>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

# chapsplit = ChapterSplitter(outputs_path=output_parent_dir)

# chapsplit.split(infile=f"{output_parent_dir}/json/combined_blocks_augmented.json")

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




