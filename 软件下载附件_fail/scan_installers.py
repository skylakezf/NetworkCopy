#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Python script to scan installer packages and export to Excel
"""

import os
import sys

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
from pathlib import Path

def get_product_name(file_path):
    """Get product name from installer file based on extension"""
    file_ext = os.path.splitext(file_path)[1].lower()
    
    if file_ext == ".exe":
        try:
            # Try to get product name using PowerShell for EXE files
            import subprocess
            # Use PowerShell to get file version info
            # Try FileDescription first
            ps_cmd = f"powershell -Command $file = Get-Item -Path '{file_path}'; $file.VersionInfo.FileDescription"
            result = subprocess.run(ps_cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            
            # If FileDescription is empty, try ProductName
            if result.returncode != 0 or not result.stdout.strip():
                ps_cmd = f"powershell -Command $file = Get-Item -Path '{file_path}'; $file.VersionInfo.ProductName"
                result = subprocess.run(ps_cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            
            # If ProductName is empty, try Description
            if result.returncode != 0 or not result.stdout.strip():
                ps_cmd = f"powershell -Command $file = Get-Item -Path '{file_path}'; $file.VersionInfo.Description"
                result = subprocess.run(ps_cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            return "无法提取产品名称"
        except Exception as e:
            return f"提取信息失败: {str(e)}"
    elif file_ext == ".msi":
        try:
            # Try to get product name from MSI file using Windows Installer
            import subprocess
            # Try to get MSI information without installing it
            cmd = f"msiexec /i '{file_path}' /l*v* msi_log.txt /qn /x"
            # Run with quiet uninstall to avoid installation prompt
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore', timeout=5)
            return "MSI产品名称"
        except Exception as e:
            return f"提取信息失败: {str(e)}"
    else:
        # For other file types, try to get some basic information
        try:
            import subprocess
            # Process the file path outside the f-string
            wmic_file_path = file_path.replace(os.sep, '\\\\')
            cmd = f"wmic datafile where name='{wmic_file_path}' get FileDescription /format:list"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip().startswith('FileDescription='):
                        description = line.split('=')[1].strip()
                        if description:
                            return description
            return "不支持的文件类型"
        except Exception as e:
            return f"提取信息失败: {str(e)}"

def main():
    """Main function"""
    scan_path = r"\\qitv1534\共享文件\安装目录\3_软件\X_常用软件"
    output_file = "Installer_Packages.xlsx"
    
    # Check if the scan path exists
    if not os.path.exists(scan_path):
        print(f"错误: 扫描路径 {scan_path} 不存在。")
        sys.exit(1)
    
    # Prepare data list
    data = []
    
    # Scan all files in the directory and subdirectories
    print(f"正在扫描 {scan_path}...")
    for root, _, files in os.walk(scan_path):
        for file in files:
            file_path = os.path.join(root, file)
            product_name = get_product_name(file_path)
            data.append({
                "文件名": file,
                "产品名称": product_name,
                "安装包路径": file_path
            })
    
    # Create DataFrame and export to Excel
    df = pd.DataFrame(data)
    df.to_excel(output_file, index=False, engine='openpyxl')
    
    print(f"扫描完成。结果已保存到 {output_file}")
    print(f"共找到 {len(data)} 个安装包文件")

if __name__ == "__main__":
    main()
