'''
处理日志文件
'''

import re
from datetime import datetime
from common.logo import logo

if __name__ == '__main__':
    logo()
    # 读取日志文件
    with open('logs/selenium.log', 'r') as file:
        log_content = file.readlines()

    # 定义日期提取的正则表达式
    date_pattern = r'endupdate: (\d{4}-\d{2}-\d{2})'

    # 提取日期大于 '2020-10-18' 的内容
    filtered_logs = []
    for line in log_content:
        match = re.search(date_pattern, line)
        if match:
            log_date = datetime.strptime(match.group(1), '%Y-%m-%d')
            if log_date > datetime(2020, 10, 18):
                filtered_logs.append((log_date, line.strip()))

    # 按日期的最新顺序排序
    filtered_logs.sort(key=lambda x: x[0], reverse=True)

    # 输出排序后的日志内容
    for log_date, log_line in filtered_logs:
        print(log_line)