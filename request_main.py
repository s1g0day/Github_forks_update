from datetime import datetime
from bs4 import BeautifulSoup
from common.logo import logo
from common.Requests_func import req_get

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

def github_commits(forks_href_url, branches):
    # print("\t\t└─3、获取commits")

    commits_url =  f"\t\t{forks_href_url}/commits/{branches}"
    print(commits_url)
    # 假设html_content是包含HTML内容的字符串
    html_content = request_test(commits_url)

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
        
        print("\t\t\t最后更新时间:", formatted_date)
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
    print("└─0、获取目标项目最后更新时间")
    github_branches(target_url)

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
    target_url = "https://github.com/phachon/mm-wiki"
    github_fork(target_url)
