import opendataloader_pdf

# C:\Users\lenox\OneDrive\Documents\tom\papers

# Batch all files in one call — each convert() spawns a JVM process, so repeated calls are slow
# opendataloader_pdf.convert(
#     input_path=[r"C:\Users\lenox\tomass\papers\The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles.pdf"],
#     output_dir="output/",
#     format="markdown, json"
# )

# # Hybrid Mode: #1 Accuracy for Complex PDFs

# pip install -U "opendataloader-pdf[hybrid]"

# opendataloader-pdf-hybrid --port 5002

# opendataloader_pdf.convert(
#     input_path=[r"C:\Users\lenox\tomass\papers\The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles.pdf"],
#     output_dir="output/",
#     hybrid="docling-fast",
#     format="pdf"
# )

## Create a class wrapper for the opendataloader-pdf library

class OpenDataParser:
    def __init__(self, outputs_path):
        self.outputs_path = outputs_path
        # self.pdfs = pdfs

    def parse(self, pdf, mode='docling-fast', output_format="json, markdown, pdf", figures_dir = "figures"):
        # parser model call
        return opendataloader_pdf.convert(
            input_path=[pdf],
            output_dir=self.outputs_path,
            hybrid=mode,
            markdown_with_html=True,
            image_dir = f"{self.outputs_path}/{figures_dir}",
            format=output_format
        )
