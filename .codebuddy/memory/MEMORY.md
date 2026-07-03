# 项目记忆

## 项目概述
- 项目名：磁盘拷贝工具
- 运行环境：Windows PE + 嵌入式 Python
- GUI 框架：Tkinter（由 Tkinter 布局助手生成）
- 功能：源设备通过网络将 D/E/F 盘数据拷贝至目标设备，并校验

## 技术栈
- GUI: Tkinter + ttk（标准库）
- 网卡枚举: ctypes + iphlpapi.dll
- IP 配置: subprocess(netsh) → fallback ctypes API
- 磁盘枚举: ctypes + DeviceIoControl
- 网络传输: http.server + urllib.request
- 校验: hashlib.md5 + csv
- 并发: threading

## 代码架构
- main.py → 入口
- ui.py → 纯布局
- control.py → 业务逻辑层
- 模块: nic_scanner / ip_config / disk_scanner / file_transfer / verifier / logger

## 关键约束
- PE 无 WMI、无 pip，尽量只用标准库
- 盘符映射是核心难点，需用户手动指定
- 源 IP 10.0.0.1/24，目标 IP 10.0.0.2/24
