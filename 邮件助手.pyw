# 双击打开邮件助手（不显示命令行窗口）。运行日志在 userdata/app.log。
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mailer.webapp import main

main()
