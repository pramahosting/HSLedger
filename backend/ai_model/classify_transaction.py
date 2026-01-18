import streamlit as st
import pandas as pd
import ollama
import time

def classify_with_ollama(model_name, description, system_prompt=None):
    """
    Sends description to your fine-tuned Ollama model.
    """
    # Default system prompt if not provided
    if system_prompt is None:
        system_prompt = "You are a financial assistant. Classify the transaction description into: Food, Travel, Shopping, Groceries, Income, or Utilities."
    
    try:
        response = ollama.generate(
            model=model_name,
            system=system_prompt,
            prompt=f"Transaction: {description}",
            options={"temperature": 0} # Keep results deterministic
        )
        return response['response'].strip()
    except Exception as e:
        return f"Error: {e}"

def render():
    st.set_page_config(page_title="Bank Transaction Classifier", layout="wide")
    st.title("Bank Transaction Classifier")
    st.write("Upload Bank Transaction CSV to automatically categorize spending.")

    # Sidebar: Model Selection
    with st.sidebar:
        st.header("Settings")
        try:
            models = [m.model for m in ollama.list().models]
            selected_model = st.selectbox("Select your Fine-Tuned Model", options=models)
        except:
            st.error("Ollama not detected. Ensure Ollama is running locally.")
            selected_model = None

    # Main UI
    uploaded_file = st.file_uploader("Upload Your CSV File", type=["csv"])
    
    if uploaded_file and selected_model:
        df = pd.read_csv(uploaded_file)
        
        # Verify the CSV structure
        if 'Description' in df.columns:
            st.success("CSV Loaded Successfully!")
            st.dataframe(df.head(5)) # Show preview
            
            if st.button("🚀 Run Classification"):
                results = []
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                start_time = time.time()
                
                for i, row in df.iterrows():
                    # Update progress every 5 rows to save UI performance
                    if i % 5 == 0:
                        progress_bar.progress((i + 1) / len(df))
                        status_text.text(f"Processing row {i+1} of {len(df)}...")
                    
                    category = classify_with_ollama(selected_model, row['Description'])
                    results.append(category)
                
                df['Predicted_Category'] = results
                duration = time.time() - start_time
                
                st.success(f"Done! Processed {len(df)} rows in {duration:.2f} seconds.")
                st.dataframe(df)
                
                # Download button
                csv_data = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="Download Categorized CSV",
                    data=csv_data,
                    file_name="categorized_transactions.csv",
                    mime="text/csv"
                )
        else:
            st.error("The uploaded CSV is missing the 'Description' column.")

if __name__ == "__main__":
    render()