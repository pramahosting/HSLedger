import pandas as pd
import json
import torch
from unsloth import FastLanguageModel
from trl import SFTTrainer
from transformers import TrainingArguments
from datasets import load_dataset
from unsloth.chat_templates import get_chat_template
import os

def convert_csv_to_jsonl(csv_path, output_jsonl):
    df = pd.read_csv(csv_path)
    df = df.dropna(subset=['Description'])

    with open(output_jsonl, 'w') as f:
        for _, row in df.iterrows():
            structured_answer = {
                "business_name": row['*Name'],
                "service_type": row['*Type']
            }

            payload = {
                "messages": [
                    {"role": "system", "content": "Extract Business Name and Service Type as JSON."},
                    {"role": "user", "content": str(row['Description'])},
                    {"role": "assistant", "content": json.dumps(structured_answer)}
                ]
            }
            f.write(json.dumps(payload) + "\n")

    return output_jsonl


def train_model(jsonl_path, output_dir = "models", model_name="unsloth/Llama-3.2-3B-Instruct", max_steps = 60):
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=model_name,
        max_seq_length=2048,
        load_in_4bit=True,
    )

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj"
        ],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
    )

    tokenizer = get_chat_template(tokenizer, chat_template="llama-3.1")

    def formatting_prompts_func(examples):
        convos = examples["messages"]
        texts = [
            tokenizer.apply_chat_template(
                convo,
                tokenize=False,
                add_generation_prompt=False
            )
            for convo in convos
        ]
        return {"text": texts}

    dataset = load_dataset("json", data_files=jsonl_path, split="train")
    dataset = dataset.map(formatting_prompts_func, batched=True)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=2048,
        args=TrainingArguments(
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=5,
            max_steps=60,
            learning_rate=2e-4,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            output_dir=output_dir,
        ),
    )

    trainer.train()

    model_path = os.path.join(output_dir, "llama3_custom")
    model.save_pretrained_gguf(
        model_path,
        tokenizer,
        quantization_method="q4_k_m"
    )

    return model_path
