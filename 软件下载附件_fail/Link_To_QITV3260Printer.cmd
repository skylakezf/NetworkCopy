rem === Printers ===
echo.正在添加网络打印机 GTMCPrinter
echo.正在访问 \\QITV3260 服务器...
rem 添加网络打印机
rundll32 printui.dll,PrintUIEntry /in /n "\\QITV3260\GTMCPrinter"
rem 将打印机设置为默认打印机
rundll32 printui.dll,PrintUIEntry /y /n "\\QITV3260\GTMCPrinter"
echo.打印机添加完成并已设置为默认打印机
