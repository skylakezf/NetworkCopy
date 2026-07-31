#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python script to compare installed programs and pre-installed programs
"""

import os
import sys
import pandas as pd

# Install required dependencies
required_packages = ['pandas', 'openpyxl']
for package in required_packages:
    try:
        __import__(package)
    except ImportError:
        print(f"Installing {package}...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

# Now import the required modules
import pandas as pd

def main():
    """Main function"""
    # Define base path (same as out.cmd)
    base_path = r"F:\Appl"
    
    # Find the latest date folder in F:\Appl
    import re
    import datetime
    
    # Check if base path exists
    if not os.path.exists(base_path):
        print(f"Error: Base path {base_path} does not exist.")
        sys.exit(1)
    
    # Get all directories in base path
    dirs = [d for d in os.listdir(base_path) if os.path.isdir(os.path.join(base_path, d))]
    
    # Filter directories that match YYYY-MM-DD format
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}$')
    date_dirs = [d for d in dirs if date_pattern.match(d)]
    
    if not date_dirs:
        print(f"Error: No folders in YYYY-MM-DD format found in {base_path}.")
        sys.exit(1)
    
    # Find the latest date folder
    latest_date = max(date_dirs, key=lambda x: datetime.datetime.strptime(x, "%Y-%m-%d"))
    output_folder = os.path.join(base_path, latest_date)
    
    # Define file paths
    preinstalled_file = "Installed.xlsx"
    installed_csv_path = os.path.join(output_folder, "Installed_Programs_Sheet1.csv")
    output_file = os.path.join(output_folder, "Required-Programs.xlsx")
    
    # Print the folder path for debugging
    print(f"Using output folder: {output_folder}")
    
    # Check if files exist
    if not os.path.exists(preinstalled_file):
        print(f"Error: Pre-installed programs file {preinstalled_file} does not exist.")
        sys.exit(1)
    
    if not os.path.exists(installed_csv_path):
        print(f"Error: Currently installed programs file {installed_csv_path} does not exist.")
        sys.exit(1)
    
    # Read pre-installed programs
    print(f"Reading pre-installed programs file: {preinstalled_file}...")
    df_preinstalled = pd.read_excel(preinstalled_file)
    
    # Print column names to debug
    print(f"Pre-installed programs file columns: {list(df_preinstalled.columns)}")
    
    # Assume pre-installed programs are in the first column
    # Try to find column with program names
    preinstalled_col = None
    for col in df_preinstalled.columns:
        if '程序' in col or '名称' in col or 'program' in col.lower():
            preinstalled_col = col
            break
    
    if not preinstalled_col:
        # If no obvious column found, use the first column
        preinstalled_col = df_preinstalled.columns[0]
    
    print(f"Using pre-installed programs column: {preinstalled_col}")
    
    # Read currently installed programs
    print(f"Reading currently installed programs file: {installed_csv_path}...")
    df_installed = pd.read_csv(installed_csv_path)
    
    # Print column names to debug
    print(f"Currently installed programs file columns: {list(df_installed.columns)}")
    
    # Assume currently installed programs are in 'DisplayName' column
    installed_col = 'DisplayName'
    if installed_col not in df_installed.columns:
        # If 'DisplayName' column not found, use the first column
        installed_col = df_installed.columns[0]
    
    print(f"Using currently installed programs column: {installed_col}")
    
    # Extract program names (strip whitespace and convert to lowercase for comparison)
    preinstalled_programs = set(df_preinstalled[preinstalled_col].dropna().str.strip().str.lower())
    installed_programs = set(df_installed[installed_col].dropna().str.strip().str.lower())
    
    print(f"Number of pre-installed programs: {len(preinstalled_programs)}")
    print(f"Number of currently installed programs: {len(installed_programs)}")
    
    # Find programs that are installed but not pre-installed
    required_programs = installed_programs - preinstalled_programs
    
    print(f"Number of required programs: {len(required_programs)}")
    
    # Get the full details of required programs from the installed dataframe
    df_required = df_installed[
        df_installed[installed_col].dropna().str.strip().str.lower().isin(required_programs)
    ]
    
    # Reset index
    df_required = df_required.reset_index(drop=True)
    
    # Export to Excel
    df_required.to_excel(output_file, index=False, engine='openpyxl')
    
    print(f"Results saved to: {output_file}")
    print(f"Required programs details:")
    for idx, row in df_required.iterrows():
        print(f"{idx + 1}. {row[installed_col]}")

if __name__ == "__main__":
    main()
