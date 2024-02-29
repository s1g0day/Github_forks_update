#!/usr/bin/env python
# coding=utf-8
import time
import random
import urllib3
import requests
from common.hander_random import requests_headers
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

headers = requests_headers()

def req_get(url, cookies):
    time.sleep(random.random()*10)
    try:
        res = requests.get(url=url, cookies=cookies, headers=headers, verify=False, allow_redirects=False, timeout=(4,20))
        res.encoding = res.apparent_encoding # apparent_encoding比"utf-8"错误率更低
        return res
    except:
        print("\033[1;31mreq_get网络出错！\033[0m")
        pass

def req_post(url, data=None, header_token=None):
    try:
        if header_token:
            headers['token'] = header_token
        res = requests.post(url=url, headers=headers, verify=False, data=data, allow_redirects=False, timeout=(4,20))
        res.encoding = res.apparent_encoding # apparent_encoding比"utf-8"错误率更低
        return res
    except:
        print("\033[1;31mreq_post网络出错！\033[0m")
        pass