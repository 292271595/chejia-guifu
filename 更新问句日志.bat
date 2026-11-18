@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在从线上拉取问句日志并重建表格...
"C:\Users\hp\AppData\Local\Programs\Python\Python312\python.exe" tools\pull_logs_daily.py --days 7
echo.
echo 退出码: %errorlevel%  （0=成功 2=没填口令 3=连不上 4=没数据 5=部分失败）
echo 表格位置: data\query_log\问句日志.xlsx
echo 运行日志: data\query_log\_pull.log
pause
