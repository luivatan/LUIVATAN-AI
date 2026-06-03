try:
    from datasets import load_dataset  # type: ignore[import]
except ImportError as e:
    raise ImportError("Missing dependency: please install the 'datasets' package (pip install datasets)") from e

from transformers import AutoTokenizer, AutoModelForCausalLM, TrainingArguments

try:
    from peft import LoraConfig, get_peft_model  # type: ignore[import]
except ImportError as e:
    raise ImportError("Missing dependency: please install the 'peft' package (pip install peft)") from e

from trl import SFTTrainer

model_name = "mistralai/Mistral-7B-v0.1"

# Load tokenizer
tokenizer = AutoTokenizer.from_pretrained(model_name)

# Load model
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    load_in_4bit=True,
    device_map="auto"
)

# LoRA config
peft_config = LoraConfig(
    r=8,
    lora_alpha=16,
    target_modules=["q_proj", "v_proj"],
    lora_dropout=0.05,
    bias="none",
    task_type="CAUSAL_LM"
)

# Apply LoRA
model = get_peft_model(model, peft_config)

# Load dataset
dataset = load_dataset("json", data_files="medical.jsonl")

# Training settings
training_args = TrainingArguments(
    output_dir="./medical-lora",
    per_device_train_batch_size=1,
    num_train_epochs=1,
    logging_steps=1,
    save_steps=10
)

# Trainer
trainer = SFTTrainer(
    model=model,
    train_dataset=dataset["train"],
    args=training_args
)

trainer.train()
