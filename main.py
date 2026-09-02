from nu_extract_parser import NuExtractParser

from markdown_to_json import JSONMaker

from json_post_process import JSONPostProcessor

from augment_json import AugmentJSON

from split_chapters import ChapterSplitter

from chapter_chunker import ChapterChunker

from docling_extract_figures import DoclingPdfPageProcessor

import os, json

from pathlib import Path

import logging

_log = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< Lopading in the PDF file, parsing into markdown then to JSON and cleaning the JSON up >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
#pdf_name = "The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles"
#pdf_name = "pelvic_nonresponse_following_treatment_of_adult.7"
#pdf_name = "Lower_Limb_Khalife"
#pdf_name = "optimizing_the_definition_of_proximal_junctional.7"
pdf_name = "posterior_ligamentous_augmentation_is_associated.9"

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
<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<< parse separately with Docling to extract images of figures and tables>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
"""

"""
 1. Extracting the content (aim to get figure and table crops from the pdf)
 Fixes the problem of the NuExtract parsed doc not having images and tables being unreliable
"""

docling_outputs_dir = f"{output_parent_dir}/docling_outputs/pages"

docling_outputs_path = Path(docling_outputs_dir)

docling_processor = DoclingPdfPageProcessor(output_dir=docling_outputs_dir)

docling_processor.process_pdf(input_pdf_path=Path(input_pdf_dir))

"""
 2. The following JSON Augmentation deals with two separate issues:
    a.) Building mappings of the images and tables found separately in the Nuextract parsed document and the extraction with docling.
    b.) The NuExtract json lacks actual images linked to the figures and tables are also imperfect, hence better replaced with links to images.

    So the augumentation a) builds mappings and b) replaces the figures and tables entries in the NuExtract json with images gained from docling extraction.

"""
# augment = AugmentJSON(images_dir=docling_outputs_path)

# augment.run(nuext_input_json=cleaned_json_path)

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<Splitting the JSON file into separate files for each chapter and adding token amt to each block>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

# chapsplit = ChapterSplitter(outputs_path=output_parent_dir)

# chapsplit.split(infile=f"{output_parent_dir}/json/combined_blocks_augmented.json")

# <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<Splitting the JSON file into separate files for each chapter>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

# chunker = ChapterChunker(  
#     model_name="Qwen2.5-14B-Instruct",  
#     token_limit=350,
#     soft_margin=0.10,  
# )

# for file in os.listdir(f"output/{pdf_name}/chapters"):
#     if file.endswith("json"):
#         with open(f"output/{pdf_name}/chapters/{file}", "r", encoding="utf-8") as f:
#             chapter_json = json.load(f)
#         chunker.chunk_and_save(chapter_json, f"output/{pdf_name}/chunks")




