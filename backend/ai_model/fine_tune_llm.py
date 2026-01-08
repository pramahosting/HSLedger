import streamlit as st
import os
import ollama  # Required to fetch local models
# from train_ollama import convert_csv_to_jsonl, train_model
from backend.ai_model.train_ollama import convert_csv_to_jsonl, train_model

def get_local_models():
    """Fetches a list of locally installed Ollama models."""
    try:
        # Get the list response
        response = ollama.list()
        
        # In newer versions, response.models is a list of Model objects
        # We access the name using the .model attribute
        return [m.model for m in response.models]
        
    except Exception as e:
        # If the above fails, try the older dictionary-style access as a fallback
        try:
            models_info = ollama.list()
            return [model['name'] for model in models_info.get('models', [])]
        except:
            st.error(f"Error fetching Ollama models: {e}")
            return ["unsloth/Llama-3.2-3B-Instruct"]

def render():
    st.set_page_config(page_title="LLM Fine-Tuning UI", layout="centered")

    st.title("🦙 LLaMA Fine-Tuning with CSV")
    st.write("Upload a CSV file and automatically fine-tune a custom LLaMA model.")

    uploaded_file = st.file_uploader(
        "Upload CSV file",
        type=["csv"]
    )

    # Fetch locally installed models
    local_models = get_local_models()
    
    # Replace st.text_input with st.selectbox
    model_name = st.selectbox(
        "Select Base Model (Local Ollama Models)",
        options=local_models,
        index=0 if local_models else None
    )

    max_steps = st.slider("Training steps", 20, 300, 60)

    if uploaded_file:
        os.makedirs("data", exist_ok=True)
        os.makedirs("models", exist_ok=True)

        csv_path = f"data/{uploaded_file.name}"
        with open(csv_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        st.success("CSV uploaded successfully!")

        if st.button("🚀 Start Training"):
            with st.spinner("Converting CSV → JSONL..."):
                jsonl_path = convert_csv_to_jsonl(
                    csv_path,
                    "data/training_data.jsonl"
                )

            st.success("Conversion complete!")

            with st.spinner(f"Training model {model_name} (this may take time)..."):
                model_path = train_model(
                    jsonl_path=jsonl_path,
                    model_name=model_name,
                    max_steps=max_steps
                )
            st.success(f"Training complete! Model saved to: {model_path}")

if __name__ == "__main__":
    render()