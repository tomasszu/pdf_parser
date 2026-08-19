from transformers import AutoTokenizer

tokenizer_qwen = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-14B-Instruct")
tokenizer_mistral = AutoTokenizer.from_pretrained("mistralai/Mistral-Small-3.1-24B-Instruct-2503")
#tokenizer_llama = AutoTokenizer.from_pretrained("meta-llama/Meta-Llama-3-8B-Instruct")


def count_tokens(text: str) -> dict:
    token_dict = {
        "Qwen2.5-14B-Instruct" : len(tokenizer_qwen.encode(text)),
        "mistralai/Mistral-Small-3.1-24B-Instruct-2503" : len(tokenizer_mistral.encode(text))
    }
    return token_dict

print("Tokenizer test")
block_text = "some paragraph..."  
print(count_tokens(block_text))