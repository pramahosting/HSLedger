import pandas as pd

# Read the CSV file
df = pd.read_csv('data/TF1_ANZ_Savings_Aug_Sep_2025.csv')

# Remove "ANZ" from the Description column
df['Description'] = df['Description'].str.replace(' ANZ$', '', regex=True)

# Remove duplicate rows (keep only distinct transactions)
df_distinct = df.drop_duplicates()

# Save the processed data
output_file = 'data/TF1_ANZ_Savings_Aug_Sep_2025_Processed.csv'
df_distinct.to_csv(output_file, index=False)

# Print summary
print(f"Original rows: {len(df)}")
print(f"Distinct rows: {len(df_distinct)}")
print(f"Duplicates removed: {len(df) - len(df_distinct)}")
print(f"\nProcessed file saved to: {output_file}")
print(f"\nFirst few rows of processed data:")
print(df_distinct.head(10))
