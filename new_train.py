import pandas as pd
import json

def convert_chart_of_accounts_to_jsonl(csv_input_path, jsonl_output_path):
    """
    Converts a Chart of Accounts CSV to a Llama-3.2-Instruct compatible JSONL file.
    Maps both '*Name' and 'Description' to the '*Type' column.
    """
    # Load the CSV
    df = pd.read_csv(csv_input_path)

    system_prompt = "You are an accounting assistant. Categorize the input into its corresponding account type."
    dataset = []

    for _, row in df.iterrows():
        # Extract fields
        name = row.get('*Name')
        acc_type = row.get('*Type')
        desc = row.get('Description')

        # Inner helper to format the Llama-3.2 Chat Template
        def create_entry(user_query, target_type):
            text = (
                f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n\n{system_prompt}<|eot_id|>"
                f"<|start_header_id|>user<|end_header_id|>\n\n{user_query}<|eot_id|>"
                f"<|start_header_id|>assistant<|end_header_id|>\n\n{target_type}<|eot_id|>"
            )
            return {"text": text}

        # 1. Create pair for Account Name
        if pd.notna(name) and pd.notna(acc_type):
            dataset.append(create_entry(f"Account Name: {name}", acc_type))

        # 2. Create pair for Description (if it exists)
        if pd.notna(desc) and str(desc).strip() != "" and pd.notna(acc_type):
            dataset.append(create_entry(f"Account Description: {desc}", acc_type))

    # Save as JSONL
    with open(jsonl_output_path, 'w') as f:
        for entry in dataset:
            f.write(json.dumps(entry) + '\n')

    print(f"Conversion complete! Created {len(dataset)} training examples in {jsonl_output_path}")

# Usage:
convert_chart_of_accounts_to_jsonl('ChartOfAccounts.csv', 'llama_3_2_dataset.jsonl')



from unsloth import FastLanguageModel
import torch
from trl import SFTTrainer
from transformers import TrainingArguments
from datasets import load_dataset
from unsloth.chat_templates import get_chat_template

# 1. Load Model
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/Llama-3.2-1B-Instruct",
    max_seq_length = 512,
    load_in_4bit = True,
)

# 2. Add LoRA Adapters
model = FastLanguageModel.get_peft_model(
    model,
    r = 16,
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha = 16,
    lora_dropout = 0,
    bias = "none",
)

# 3. Load & Format Dataset
tokenizer = get_chat_template(tokenizer, chat_template = "llama-3.1") # Compatible with 3.2

def formatting_prompts_func(examples):
    convos = examples["messages"]
    texts = [tokenizer.apply_chat_template(convo, tokenize = False, add_generation_prompt = False) for convo in convos]
    return { "text" : texts }

dataset = load_dataset("json", data_files="llama_3_2_dataset.jsonl", split="train")
# dataset = dataset.map(formatting_prompts_func, batched = True)

# 4. Run Trainer
trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    dataset_text_field = "text",
    max_seq_length = 2048,
    args = TrainingArguments(
        per_device_train_batch_size = 4,
        gradient_accumulation_steps = 4,
        warmup_steps = 10,
        max_steps = 120, # Increase to 120+ for better accuracy
        learning_rate = 1e-4,
        fp16 = not torch.cuda.is_bf16_supported(),
        bf16 = torch.cuda.is_bf16_supported(),
        logging_steps = 1,
        optim = "adamw_8bit",
        weight_decay = 0.01,
        output_dir = "outputs",
    ),
)
trainer.train()