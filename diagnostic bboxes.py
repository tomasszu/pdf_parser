import fitz  
import json

pdf_name = "The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles"

input_path = f"C:/Users/lenox/tomass/papers/{pdf_name}.pdf"

# Load your JSON  
with open("output\The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles\The_Shape_of_the_Fused_Spine_is_Associated_With_Acute_Proximal_Junctional_Kyphosis_in_Adult_Spinal_Deformity_An_Assessment_Based_on_Vertebral_Pelvic_Angles.json", "r", encoding="utf-8") as f:  
    data = json.load(f)

doc = fitz.open(input_path)

for entry in data.get("kids", []):  
    if entry.get("type") == "table":  
        page_num = entry["page number"]  
        bbox = entry["bounding box"] # [x0, y0, x1, y1]  
          
        page = doc[page_num-1]  
          
        # --- TEST 1: Direct coordinates (Top-Left) ---  
        # rect = fitz.Rect(bbox[0], bbox[1], bbox[2], bbox[3])  
          
        # --- TEST 2: If Test 1 is wrong, try Y-Inversion (Bottom-Left) ---  
        page_height = page.rect.height  
        rect = fitz.Rect(bbox[0], page_height - bbox[3], bbox[2], page_height - bbox[1])

        # Draw a red rectangle on the PDF page  
        page.draw_rect(rect, color=(1, 0, 0), width=2)  
        page.insert_text((bbox[0], page_height - bbox[3] - 5), f"Table {entry['id']}", color=(1, 0, 0))

doc.save("diagnostic.pdf")