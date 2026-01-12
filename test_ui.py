import streamlit as st
import ollama  # pip install ollama

# 1. Pop-up Dialog for Model Selection
@st.dialog("Configure Local Classifier")
def select_model_dialog(input_text):
    st.write("Fetching models from your local Ollama server...")
    
    try:
        # Automatically get models you've already pulled (llama3, mistral, etc.)
        # model_list = ollama.list().models
        models = [m.model for m in ollama.list().models]
        
        if not models:
            st.warning("No models found. Run 'ollama pull llama3' in your terminal.")
            return

        selected = st.selectbox("Select local engine:", models)
        
        if st.button("Start Classification"):
            st.session_state.selected_model = selected
            st.session_state.run_inference = True
            st.rerun()
            
    except Exception as e:
        st.error(f"Could not connect to Ollama: {e}")

# 2. Main UI Layout
st.set_page_config(page_title="Local LLM Classifier", page_icon="🤖")
st.title("Local Text Classifier")

user_text = st.text_area("Enter content to classify:", height=150)

if st.button("Classify"):
    if user_text.strip():
        select_model_dialog(user_text)
    else:
        st.error("Please enter some text first.")

# 3. Handling the Classification Logic
if st.session_state.get("run_inference"):
    model = st.session_state.selected_model
    
    with st.status(f"Classifying with {model}...", expanded=True) as status:
        st.write("Sending request to local API...")
        
        # Simple Zero-Shot Classification Prompt
        prompt = f"Classify the following text into a single category (e.g., Tech, Sports, Politics). Text: {user_text}"
        
        response = ollama.generate(model=model, prompt=prompt)
        print(response)
        st.subheader("Result:")
        st.success(response['response'])
        status.update(label="Classification Complete!", state="complete")
    
    # Reset state so it doesn't re-run on every click
    st.session_state.run_inference = False