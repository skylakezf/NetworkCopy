@echo off
REM 切换到本批处理文件所在目录 (项目根目录)
cd /d "%~dp0"

REM 用嵌入式 Python 调用 PyInstaller 打包
.\python-3.13.14-embed-amd64\python.exe -m PyInstaller --onefile --windowed --name "磁盘拷贝工具" --hidden-import tkinter --hidden-import tkinter.ttk --hidden-import urllib.parse --hidden-import json --hidden-import csv --hidden-import hashlib --hidden-import concurrent.futures --hidden-import http.server --hidden-import struct --hidden-import re --hidden-import threading --hidden-import socket --hidden-import subprocess --hidden-import ctypes --hidden-import ctypes.wintypes --hidden-import urllib.request --hidden-import urllib.error --hidden-import cryptography --hidden-import cryptography.hazmat.primitives.asymmetric.rsa --hidden-import cryptography.hazmat.primitives.serialization --hidden-import cryptography.hazmat.backends.openssl.backend --hidden-import secrets --hidden-import tls_utils --collect-data cryptography main.py


