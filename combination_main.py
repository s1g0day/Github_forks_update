import logging
from common.logo import logo
from common.Requests_func import req_get
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from datetime import datetime

def selenium_request_test(url):
    # 设置 Chrome WebDriver 的选项
    chrome_options = Options()
    chrome_options.use_chromium = True

    chrome_options.add_argument("–incognito") # 隐身模式（无痕模式）
    chrome_options.add_argument('--headless') # 无界模式
    chrome_options.add_argument("blink-settings=imagesEnabled=false") # 不加载图片
    chrome_options.add_experimental_option("excludeSwitches",["enable-logging"])
    if url.startswith("https://"):
        chrome_options.add_argument('--ignore-certificate-errors') # 设置Chrome忽略网站证书错误
        chrome_options.add_argument('--ignore-ssl-errors')
    chrome_options.add_argument("--disable-dev-shm-usage")  # 禁用/dev/shm
    chrome_options.add_argument("--no-sandbox")  # 禁用沙盒模式
    chrome_options.add_argument('--remote-debugging-port=0')  # 禁用 DevTools
    service = Service('chromedriver-win64/chromedriver')  # 指定 Chrome WebDriver 的路径

    # 创建 WebDriver 对象
    driver = webdriver.Chrome(service=service, options=chrome_options)

    # 打开网页
    driver.get(url)

    # 等待页面加载完成（这里以等待标题出现为例）
    wait = WebDriverWait(driver, 10)
    wait.until(EC.presence_of_element_located((By.TAG_NAME, "title")))

    # 获取页面内容
    html_content = driver.page_source

    driver.quit()
    return html_content

def request_test(url):
    cookies = {
        '_gh_sess': 'rZDr6Xt478WeOJGHytRswTR5gseUFHuDLkHhkD9NyWWdBO3KeyR%2FJO3yNCJH23%2FY%2B7yoJmuqPL2jfnvz%2FLQyMMHftTgPCVHDnXUzM3VemFSwOaS%2Bo727FWTQoX64kTGLi2jlnx8nszZDGveOVcC8UhlBWgR7RU%2F%2FNYPjPVjvfN74M3NVzF7hyZM%2FLaLmW%2BSUU1E0gU%2FEk7YOwrun%2F3Kxmxb0Iq0V%2FNNqycxDDb0%2BIbBWjAnCtjY%2Flv4gMX%2F1kKPk8TRWWrVnpUnOtbSMLWZ2aQ%3D%3D--%2FrnSdblPFhqwW4P3--WqaEJVOn%2FdwMhnNYGFv7RA%3D%3D',
        '_octo': 'GH1.1.852016319.1709196477',
        'logged_in': 'no',
        'preferred_color_mode': 'light',
        'tz': 'Asia%2FShanghai',
    }

    response = req_get(url, cookies=cookies)
    return response.text

# 定义日志函数
def log(commits_url, formatted_date):
    
    logging.info(f"url: {commits_url}, endupdate: {formatted_date}")

def github_commits(forks_href_url, branches):
    # print("\t\t└─3、获取commits")

    commits_url =  f"{forks_href_url}/commits/{branches}"
    # print(commits_url)
    # 假设html_content是包含HTML内容的字符串
    html_content = selenium_request_test(commits_url)

    # 使用BeautifulSoup解析HTML内容
    soup = BeautifulSoup(html_content, 'html.parser')

    # 查找具有data-testid为"commit-group-title"的元素
    element = soup.find('h3', attrs={'data-testid': 'commit-group-title'})

    # 提取文本内容
    if element:
        result = element.text
        # print("Original result:", result)

        # 转换日期格式
        date_str = result.split("Commits on ")[1]
        date_obj = datetime.strptime(date_str, "%b %d, %Y")
        formatted_date = date_obj.strftime("%Y-%m-%d")
        
        print(f"\t\turl: {commits_url}, endupdate: {formatted_date}")
        log(commits_url, formatted_date)
        return result
        
    else:
        print("\t\tElement not found.")

def github_branches(forks_href_url):
    # print("\t└─2、获取branches")

    branches_url = f"\t{forks_href_url}/branches/all"
    # 假设html_content是包含HTML内容的字符串
    print(branches_url)
    html_content = request_test(branches_url)

    # 使用BeautifulSoup解析HTML内容
    soup = BeautifulSoup(html_content, 'html.parser')

    # 查找包含在TableBody类中的所有<a>标签
    table_body = soup.find('tbody', class_='TableBody')
    if table_body:
        links = table_body.find_all('a')

        for link in links:
            href_value = link['href']
            div_element = link.find('div')
            if div_element:
                title_value = div_element.get('title', 'Title not found')
                # print(f"href: {href_value}, title: {title_value}")
                github_commits(forks_href_url, title_value)
    else:
        print("TableBody not found.")

def github_fork(target_url):
    github_commits(target_url, "master")
    print("└─1、获取forks")

    forks_url = f"{target_url}/forks?include=active&page=1&period=&sort_by=last_updated"
    print(forks_url)
    # 假设html_content是包含HTML内容的字符串
    html_content = request_test(forks_url)

    # 使用BeautifulSoup解析HTML内容
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 查找所有class为"Link f4"的<a>标签
    links = soup.find_all('a', class_="Link f4")
    if links:
        # 提取href属性的值
        for link in links:
            href_value = link['href']
            # print(href_value)
            github_branches("https://github.com" + href_value)
            
    else:
        print("fork为空")
if __name__ == '__main__':
    logo()
    '''
    1.输入目标项目地址，获取fork记录，并提取出fork地址
    2.获取fork项目中的branches，提取branches值
    3.根据fork项目及branches值获取commits内容

    不足： 第三步容易获取失败
    '''
    # 配置日志
    logging.basicConfig(filename='logs/combination.log', level=logging.INFO)

    target_url = "https://github.com/phachon/mm-wiki"
    github_fork(target_url)