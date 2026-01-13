# -*- coding: UTF-8 -*-
'''
@Createtime: 2025/5/16 13:18
@Updatetime: 2026/1/13 14:28
@Version: 0.0.4
@description: 
    1. 从GitHub获取fork信息
    2. 支持并行处理
    3. 支持断点续传
    4. 支持API速率限制处理
    5. 支持跳过无差异的fork
    6. 支持获取分支信息
    7. 支持获取分支比较信息
'''

import os
import sys
import time
import argparse
import json
import sqlite3
import logging
from datetime import datetime, timezone
import pytz  # 导入pytz库
from typing import List, Dict, Tuple, Optional, Set
from github import Github
from github.Repository import Repository
from github.GithubException import RateLimitExceededException, UnknownObjectException, GithubException
from dotenv import load_dotenv
from tqdm import tqdm
import concurrent.futures
import threading
import random

# 定义上海时区
shanghai_tz = pytz.timezone('Asia/Shanghai')

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("forks_analysis.log", encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# 进度计数器锁
progress_lock = threading.Lock()
# API速率限制全局暂停信号 (Set=正常运行, Clear=暂停等待)
api_pause_event = threading.Event()
api_pause_event.set()

processed_count = 0
error_count = 0
skipped_count = 0
nonexistent_count = 0
rate_limit_count = 0  # 新添加：API限制计数器
processed_forks = set()  # 新添加：已处理的fork集合

def exponential_backoff(attempt: int, base_delay: float = 2.0, max_delay: float = 3600.0) -> float:
    """计算指数退避延迟时间"""
    delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
    return delay

def wait_with_progress(seconds: float, message: str = "等待中"):
    """显示等待进度的倒计时"""
    start_time = time.time()
    while time.time() - start_time < seconds:
        remaining = seconds - (time.time() - start_time)
        print(f"\r{message}，剩余 {remaining:.0f} 秒...  ", end="", flush=True)
        time.sleep(1)
    print("\r" + " " * 100 + "\r", end="", flush=True)  # 清除进度显示

def check_api_rate_limit(g: Github, force_wait: bool = False) -> Tuple[int, datetime]:
    """检查GitHub API速率限制状态"""
    try:
        # 如果全局暂停中，直接等待直到恢复
        if not api_pause_event.is_set():
            api_pause_event.wait()
            
        rate_limit = g.get_rate_limit()
        core_rate = rate_limit.core
        remaining = core_rate.remaining
        reset_time = core_rate.reset
        # 将reset_time转换为上海时区
        reset_time_shanghai = reset_time.astimezone(shanghai_tz)
 
        if remaining < 100 or force_wait:  # 如果剩余次数少于100或强制等待
            # 触发全局暂停
            api_pause_event.clear()
            
            # 创建带时区的UTC当前时间
            utc_now = datetime.now(timezone.utc)
            wait_time = (reset_time - utc_now).total_seconds()
            if wait_time > 0:
                message = f"⚠️ API速率即将耗尽（剩余{remaining}次）"
                logger.warning(message)
                wait_with_progress(wait_time + 1, message)  # 额外等待1秒以确保重置完成
                
            # 恢复全局运行
            api_pause_event.set()
            return check_api_rate_limit(g)  # 重新检查速率
        
        return remaining, reset_time_shanghai
    except GithubException as e:
        if e.status == 403:  # Forbidden
            api_pause_event.clear()
            logger.warning(f"\n⚠️ API访问被拒绝(403)，可能是由于速率限制或权限问题")
            # 保守等待1小时
            wait_with_progress(3600, "API访问被拒绝，等待重试")
            api_pause_event.set()
            return check_api_rate_limit(g)
    except Exception as e:
        logger.error(f"\n检查API速率限制时出错: {str(e)}")
        # 如果无法获取速率限制信息，保守等待一段时间
        wait_with_progress(300, "无法获取API速率限制信息，保守等待")
        return 0, datetime.utcnow()

def retry_with_backoff(func, *args, max_attempts: int = 5, **kwargs):
    """使用指数退避的重试机制"""
    global rate_limit_count
    
    for attempt in range(max_attempts):
        try:
            # 也就是在每次请求前，检查是否被暂停了
            api_pause_event.wait()
            return func(*args, **kwargs)
        except RateLimitExceededException:
            # 暂停所有线程
            api_pause_event.clear()
            with progress_lock:
                rate_limit_count += 1
                logger.warning(f"\n⚠️ 第 {attempt + 1} 次尝试触发API限制...")
            
            if attempt < max_attempts - 1:
                # 这里的 check_api_rate_limit 会负责 wait 并且 set api_pause_event
                # 注意：check_api_rate_limit 需要 Github 实例，但这里 args[0] 不一定是 Github 实例
                # 如果这个函数是 generic 的，我们可能无法轻易拿到 g。
                # 尝试从 args 或者 kwargs 中寻找 g，或者直接由外部处理
                # 暂时仅仅做简单的 wait
                wait_time = exponential_backoff(attempt, base_delay=30, max_delay=3600)
                wait_with_progress(wait_time, "触发速率限制，等待恢复")
                api_pause_event.set()
                
        except GithubException as e:
            if e.status == 403:  # Forbidden
                # 暂停所有线程
                api_pause_event.clear()
                
                delay = exponential_backoff(attempt, base_delay=30) # 403通常需要更长时间
                with progress_lock:
                    logger.warning(f"\n⚠️ 请求被拒绝（403），等待 {delay:.1f} 秒后重试...")
                wait_with_progress(delay, "等待重试")
                
                api_pause_event.set()
            elif e.status == 404:
                 # 404 不需要重试，直接抛出，由上层处理
                 raise 
            else:
                raise
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            delay = exponential_backoff(attempt)
            with progress_lock:
                logger.warning(f"\n⚠️ 发生错误: {str(e)}，{delay:.1f} 秒后重试...")
            wait_with_progress(delay, "等待重试")
    
    raise Exception(f"在 {max_attempts} 次尝试后仍然失败")

def get_commits_safely(repo, **kwargs):
    """安全地获取提交信息"""
    try:
        return retry_with_backoff(lambda: repo.get_commits(**kwargs).get_page(0))
    except Exception as e:
        # 降级日志级别，避免刷屏
        # logger.debug(f"无法获取提交信息 - {str(e)}")
        return []

def check_repository_exists(g: Github, repo_name: str) -> Optional[Repository]:
    """检查仓库是否存在"""
    try:
        repo = g.get_repo(repo_name)
        # 尝试访问一些基本属性来验证仓库确实存在且可访问
        _ = repo.full_name
        return repo
    except UnknownObjectException:
        return None
    except Exception as e:
        logger.error(f"检查仓库时发生错误: {str(e)}")
        return None

def load_github_token() -> str:
    """加载GitHub Token"""
    load_dotenv()
    token = os.getenv('GITHUB_TOKEN')
    if not token:
        logger.error("错误：未找到GITHUB_TOKEN。请在.env文件中设置您的GitHub Token。")
        sys.exit(1)
    return token

def get_repository_info(repo_path: str) -> Repository:
    """获取仓库信息"""
    try:
        g = Github(load_github_token())
        # 检查API速率限制
        remaining, reset_time = check_api_rate_limit(g)
        logger.info(f"当前API速率限制状态：剩余 {remaining} 次，将于上海时间 {reset_time.strftime('%Y-%m-%d %H:%M:%S')} 重置")
        
        # 检查仓库是否存在
        repo = check_repository_exists(g, repo_path)
        if not repo:
            logger.error(f"错误：仓库 {repo_path} 不存在或无法访问")
            sys.exit(1)
        return repo
    except Exception as e:
        logger.error(f"错误：无法获取仓库信息 - {str(e)}")
        sys.exit(1)

def save_progress(repo_path: str, forks_info: List[Dict], processed_fork_names: Set[str]):
    """保存当前进度到文件"""
    progress_file = f"{repo_path.replace('/', '_')}_progress.json"
    data = {
        'repo_path': repo_path,
        'forks_info': forks_info,
        'processed_fork_names': list(processed_fork_names),
        'timestamp': datetime.now(shanghai_tz).strftime('%Y-%m-%d %H:%M:%S')
    }
    
    try:
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        logger.info(f"✅ 进度已保存到文件: {progress_file}")
    except Exception as e:
        logger.error(f"❌ 保存进度时出错: {str(e)}")

def load_progress(repo_path: str) -> Tuple[List[Dict], Set[str]]:
    """从文件加载之前的进度"""
    progress_file = f"{repo_path.replace('/', '_')}_progress.json"
    
    if not os.path.exists(progress_file):
        return [], set()
    
    try:
        with open(progress_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 验证数据是否属于当前仓库
        if data.get('repo_path') != repo_path:
            logger.warning(f"\n⚠️ 进度文件不匹配当前仓库，将重新开始")
            return [], set()
        
        forks_info = data.get('forks_info', [])
        processed_fork_names = set(data.get('processed_fork_names', []))
        timestamp = data.get('timestamp', '未知时间')
        
        logger.info(f"\n✅ 已加载之前的进度 (保存于 {timestamp})")
        logger.info(f"已处理 {len(processed_fork_names)} 个fork，已收集 {len(forks_info)} 个结果")
        
        return forks_info, processed_fork_names
    except Exception as e:
        logger.error(f"\n⚠️ 加载进度时出错: {str(e)}，将重新开始")
        return [], set()

def process_fork(g: Github, repo: Repository, fork, total_forks: int, show_comparison: bool, skip_no_diff: bool, check_branches: bool = True) -> Dict:
    """处理单个fork的信息，返回处理结果"""
    global processed_count, error_count, skipped_count, nonexistent_count, processed_forks
    
    # 检查是否已经处理过这个fork
    if fork.full_name in processed_forks:
        with progress_lock:
            # logger.debug(f"已跳过: {fork.full_name} (之前已处理)")
            pass
        return None
    
    try:
        # 检查fork是否仍然存在
        fork_repo = retry_with_backoff(check_repository_exists, g, fork.full_name)
        if not fork_repo:
            with progress_lock:
                nonexistent_count += 1
                processed_count += 1
                processed_forks.add(fork.full_name)  # 添加到已处理集合
                logger.info(f"处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork.full_name} (仓库不存在，跳过)")
            return None
        
        # 获取最后一次提交时间
        commits = get_commits_safely(fork_repo)
        if commits:
            last_commit_date = commits[0].commit.author.date
        else:
            last_commit_date = fork_repo.updated_at
        
        fork_info = {
            'name': fork_repo.full_name,
            'url': fork_repo.html_url,
            'stars': fork_repo.stargazers_count,
            'forks': fork_repo.forks_count,
            'last_updated': last_commit_date,
            'description': fork_repo.description or "无描述",
            'default_branch': fork_repo.default_branch,
            'ahead_by': None,
            'behind_by': None,
            'branches': [],
            'branch_comparisons': {}
        }
        
        # 获取分支信息
        if check_branches:
            try:
                branches = retry_with_backoff(lambda: list(fork_repo.get_branches()))
                fork_info['branches'] = [branch.name for branch in branches]
                
                # 对每个分支进行比较
                if show_comparison:
                    for branch in branches:
                        try:
                            comparison = retry_with_backoff(
                                lambda: repo.compare(repo.default_branch, f"{fork_repo.owner.login}:{branch.name}")
                            )
                            fork_info['branch_comparisons'][branch.name] = {
                                'ahead_by': comparison.ahead_by,
                                'behind_by': comparison.behind_by
                            }
                            
                            if branch.name == fork_repo.default_branch:
                                fork_info['ahead_by'] = comparison.ahead_by
                                fork_info['behind_by'] = comparison.behind_by
                                
                                if skip_no_diff and comparison.ahead_by == 0 and comparison.behind_by == 0:
                                    with progress_lock:
                                        skipped_count += 1
                                        processed_count += 1
                                        logger.info(f"处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork_repo.full_name} (无差异，跳过)")
                                    return None
                        
                        except GithubException as e:
                            # 忽略404 Not Found (例如分支没有共同祖先)
                            if e.status == 404:
                                # logger.debug(f"无法比较分支 {branch}: {str(e)}")
                                pass
                            else:
                                logger.warning(f"无法比较分支 {branch.name} - {str(e)}")
                        except Exception as e:
                            logger.warning(f"无法比较分支 {branch.name} - {str(e)}")
                            continue
            except Exception as e:
                logger.warning(f"无法获取分支信息 - {str(e)}")
        else:
            # 如果不检查分支，但需要比较默认分支
            if show_comparison:
                try:
                    comparison = retry_with_backoff(
                        lambda: repo.compare(repo.default_branch, f"{fork_repo.owner.login}:{fork_repo.default_branch}")
                    )
                    fork_info['ahead_by'] = comparison.ahead_by
                    fork_info['behind_by'] = comparison.behind_by
                    
                    if skip_no_diff and comparison.ahead_by == 0 and comparison.behind_by == 0:
                        with progress_lock:
                            skipped_count += 1
                            processed_count += 1
                            logger.info(f"处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork_repo.full_name} (无差异，跳过)")
                        return None
                except Exception as e:
                    if isinstance(e, GithubException) and e.status == 404:
                        pass
                    else:
                        logger.warning(f"无法比较默认分支 - {str(e)}")
    
        with progress_lock:
            processed_count += 1
            processed_forks.add(fork.full_name)  # 添加到已处理集合
            progress = f"处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork_repo.full_name}"
            if fork_info['ahead_by'] is not None:
                progress += f" (领先: {fork_info['ahead_by']}, 落后: {fork_info['behind_by']})"
            logger.info(progress)
            
        return fork_info
    except Exception as e:
        with progress_lock:
            error_count += 1
            processed_count += 1
            processed_forks.add(fork.full_name)  # 添加到已处理集合
            logger.error(f"处理 {fork.full_name} 时出错 - {str(e)}")
        return None

def get_forks_info(repo: Repository, max_forks: int = None, workers: int = 10, show_comparison: bool = True, skip_no_diff: bool = False, resume: bool = False, check_branches: bool = True) -> List[Dict]:
    """获取所有fork的信息，支持并行处理和断点续传"""
    global processed_count, error_count, skipped_count, nonexistent_count, rate_limit_count, processed_forks
    
    # 初始化计数器
    if not resume:
        processed_count = 0
        error_count = 0
        skipped_count = 0
        nonexistent_count = 0
        rate_limit_count = 0
        processed_forks = set()
    
    logger.info(f"正在获取 {repo.full_name} 的fork信息...")
    
    # 加载之前的进度
    forks_info = []
    if resume:
        forks_info, processed_forks = load_progress(repo.full_name)
    
    try:
        g = Github(load_github_token())
        # 检查API速率限制
        remaining, reset_time = check_api_rate_limit(g)
        print(f"当前API速率限制状态：剩余 {remaining} 次，将于上海时间 {reset_time.strftime('%Y-%m-%d %H:%M:%S')} 重置")
        
        # 获取所有fork
        all_forks = retry_with_backoff(lambda: list(repo.get_forks()))
        total_forks = len(all_forks)
        print(f"总共找到 {total_forks} 个fork")
        
        if max_forks and max_forks < total_forks:
            all_forks = all_forks[:max_forks]
            total_forks = max_forks
            print(f"根据设置，将只处理前 {max_forks} 个fork")
        
        if resume and processed_forks:
            print(f"断点续传：已跳过 {len(processed_forks)} 个之前处理过的fork")
        
        print(f"开始处理，使用 {workers} 个并行工作线程...")
        if not check_branches:
            print("已禁用分支信息获取")
        if skip_no_diff:
            print("已启用跳过无差异的fork")
        
        start_time = time.time()
        
        # 添加信号处理，支持用户中断
        original_sigint_handler = None
        if sys.platform != 'win32':  # 在非Windows平台上使用信号处理
            import signal
            def signal_handler(sig, frame):
                print("\n\n⚠️ 用户中断，正在保存进度...")
                save_progress(repo.full_name, forks_info, processed_forks)
                print("进度已保存，可以使用 --resume 参数继续执行")
                sys.exit(0)
            
            original_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, signal_handler)
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_fork = {
                    executor.submit(process_fork, g, repo, fork, total_forks, show_comparison, skip_no_diff, check_branches): fork 
                    for fork in all_forks
                }
                
                # 定期保存进度
                last_save_time = time.time()
                save_interval = 300  # 每5分钟保存一次进度
                
                for future in concurrent.futures.as_completed(future_to_fork):
                    fork = future_to_fork[future]
                    try:
                        fork_info = future.result()
                        if fork_info:
                            forks_info.append(fork_info)
                        
                        # 检查是否需要保存进度
                        current_time = time.time()
                        if current_time - last_save_time > save_interval:
                            save_progress(repo.full_name, forks_info, processed_forks)
                            last_save_time = current_time
                            
                        # 检查API速率限制，如果即将耗尽，保存进度并退出
                        if rate_limit_count > 0 and rate_limit_count % 3 == 0:  # 每触发3次API限制检查一次
                            remaining, _ = check_api_rate_limit(g)
                            if remaining < 50:  # 如果剩余次数少于50
                                logger.warning("\n\n⚠️ API速率即将耗尽，正在保存进度...")
                                save_progress(repo.full_name, forks_info, processed_forks)
                                logger.warning("进度已保存，请稍后使用 --resume 参数继续执行")
                                logger.warning(f"建议等待API速率重置后再继续")
                                return forks_info
                            
                    except Exception as e:
                        logger.error(f"\n错误：处理fork时发生异常 - {str(e)}")
        finally:
            # 恢复原始信号处理器
            if sys.platform != 'win32' and original_sigint_handler:
                signal.signal(signal.SIGINT, original_sigint_handler)
        
        end_time = time.time()
        duration = end_time - start_time
        
        logger.info(f"\n全部处理完成！成功获取了 {len(forks_info)}/{total_forks} 个fork的信息")
        if nonexistent_count > 0:
            logger.info(f"跳过了 {nonexistent_count} 个不存在的fork")
        if skipped_count > 0:
            logger.info(f"跳过了 {skipped_count} 个无差异的fork")
        if rate_limit_count > 0:
            logger.warning(f"遇到 {rate_limit_count} 次API限制")
        logger.info(f"总耗时: {duration:.2f}秒, 平均每个fork耗时: {duration/total_forks:.2f}秒")
        if error_count > 0:
            logger.error(f"处理失败: {error_count}个")
            
        # 最后保存一次进度
        save_progress(repo.full_name, forks_info, processed_forks)
            
    except Exception as e:
        logger.error(f"错误：获取fork列表时出错 - {str(e)}")
        # 发生错误时也保存进度
        save_progress(repo.full_name, forks_info, processed_forks)
        sys.exit(1)
        
    return forks_info

def print_forks_info(forks_info: List[Dict], top_n: int = None):
    """打印fork信息，可以限制只显示前N个"""
    if not forks_info:
        logger.info("\n未找到符合条件的fork")
        return

    for fork in forks_info:
        if isinstance(fork['last_updated'], str):
            try:
                fork['last_updated'] = datetime.fromisoformat(fork['last_updated'].replace('Z', '+00:00'))
            except ValueError:
                # If conversion fails, set to a very old date to put it at the end
                fork['last_updated'] = datetime.min
    # 按最后更新时间排序
    sorted_forks = sorted(forks_info, key=lambda x: x['last_updated'], reverse=True)
    
    # 如果指定了top_n，则只显示前N个
    if top_n and top_n < len(sorted_forks):
        display_forks = sorted_forks[:top_n]
        logger.info(f"\n=== Fork 分析结果 (显示前 {top_n} 个) ===")
    else:
        display_forks = sorted_forks
        logger.info(f"\n=== Fork 分析结果 ===")
    
    logger.info(f"共 {len(sorted_forks)} 个fork，显示 {len(display_forks)} 个\n")
    
    for fork in display_forks:
        # 使用logger时换行符需要注意，这里简化输出逻辑
        info_str = []
        info_str.append(f"仓库: {fork['name']}")
        info_str.append(f"链接: {fork['url']}")
        info_str.append(f"Stars: {fork['stars']}")
        info_str.append(f"Forks: {fork['forks']}")
        info_str.append(f"最后更新: {fork['last_updated'].strftime('%Y-%m-%d %H:%M:%S')}")
        info_str.append(f"分支: {', '.join(fork['branches'])}")
        
        # 显示每个分支的比较结果
        if fork['branch_comparisons']:
            info_str.append("分支对比:")
            for branch, comparison in fork['branch_comparisons'].items():
                info_str.append(f"  - {branch}: 领先 {comparison['ahead_by']} commits, 落后 {comparison['behind_by']} commits")
        elif fork['ahead_by'] is not None and fork['behind_by'] is not None:
            info_str.append(f"默认分支对比: 领先 {fork['ahead_by']} commits, 落后 {fork['behind_by']} commits")
            
        info_str.append(f"描述: {fork['description']}")
        info_str.append("-" * 50)
        
        logger.info("\n".join(info_str))

def save_to_sqlite(forks_info: List[Dict], db_filename: str):
    """将fork信息保存到SQLite数据库"""
    if not forks_info:
        logger.info("没有数据可保存到数据库")
        return

    try:
        conn = sqlite3.connect(db_filename)
        cursor = conn.cursor()
        
        # 创建表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS forks (
            name TEXT PRIMARY KEY,
            url TEXT,
            stars INTEGER,
            forks_count INTEGER,
            last_updated TEXT,
            description TEXT,
            default_branch TEXT,
            ahead_by INTEGER,
            behind_by INTEGER,
            branches TEXT,
            branch_comparisons TEXT
        )
        ''')
        
        count = 0
        for fork in forks_info:
            # 处理时间格式
            last_updated = fork['last_updated']
            if isinstance(last_updated, datetime):
                last_updated = last_updated.strftime('%Y-%m-%d %H:%M:%S')
            
            # 处理复杂类型
            branches_json = json.dumps(fork.get('branches', []), ensure_ascii=False)
            comparisons_json = json.dumps(fork.get('branch_comparisons', {}), ensure_ascii=False)
            
            cursor.execute('''
            INSERT OR REPLACE INTO forks (
                name, url, stars, forks_count, last_updated, description,
                default_branch, ahead_by, behind_by, branches, branch_comparisons
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                fork['name'],
                fork['url'],
                fork['stars'],
                fork['forks'],
                last_updated,
                fork['description'],
                fork.get('default_branch'),
                fork.get('ahead_by'),
                fork.get('behind_by'),
                branches_json,
                comparisons_json
            ))
            count += 1
            
        conn.commit()
        conn.close()
        logger.info(f"\n成功保存 {count} 条记录到数据库: {db_filename}")
        
    except Exception as e:
        logger.error(f"保存数据库时出错: {str(e)}")

def save_to_file(forks_info: List[Dict], filename: str):
    """将fork信息保存到文件"""
    if not forks_info:
        logger.info("没有数据可保存")
        return
        
    # 按最后更新时间排序
    sorted_forks = sorted(forks_info, key=lambda x: x['last_updated'], reverse=True)
    
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("# Fork 分析结果\n\n")
            # 使用上海时区的当前时间
            current_time_shanghai = datetime.now(shanghai_tz)
            f.write(f"分析时间: {current_time_shanghai.strftime('%Y-%m-%d %H:%M:%S')} (上海时间)\n")
            f.write(f"共找到 {len(sorted_forks)} 个fork\n\n")
            
            for fork in sorted_forks:
                f.write(f"## {fork['name']}\n\n")
                f.write(f"- 链接: {fork['url']}\n")
                f.write(f"- Stars: {fork['stars']}\n")
                f.write(f"- Forks: {fork['forks']}\n")
                # 将最后更新时间转换为上海时区
                last_updated_shanghai = fork['last_updated']
                if hasattr(last_updated_shanghai, 'astimezone'):
                    last_updated_shanghai = last_updated_shanghai.astimezone(shanghai_tz)
                f.write(f"- 最后更新: {last_updated_shanghai.strftime('%Y-%m-%d %H:%M:%S')} (上海时间)\n")
                if fork['ahead_by'] is not None and fork['behind_by'] is not None:
                    f.write(f"- 领先原仓库: {fork['ahead_by']} commits\n")
                    f.write(f"- 落后原仓库: {fork['behind_by']} commits\n")
                f.write(f"- 描述: {fork['description']}\n\n")
                f.write("---\n\n")
                
        logger.info(f"\n结果已保存到文件: {filename}")
    except Exception as e:
        logger.error(f"保存文件时出错: {str(e)}")

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(description='分析GitHub仓库的Fork情况')
    parser.add_argument('repo', help='仓库路径，格式为 owner/repo', nargs='?')
    parser.add_argument('-m', '--max', type=int, help='最多处理的fork数量', default=None)
    parser.add_argument('-w', '--workers', type=int, help='并行处理的线程数', default=10)
    parser.add_argument('-t', '--top', type=int, help='只显示前N个结果', default=None)
    parser.add_argument('-o', '--output', help='将结果保存到指定文件', default=None)
    parser.add_argument('-nc', '--no-compare', action='store_true', help='不获取与原仓库的比较数据，加快处理速度')
    parser.add_argument('-s', '--skip-no-diff', action='store_true', help='跳过无差异的fork')
    parser.add_argument('-r', '--resume', action='store_true', help='从上次中断的地方继续执行')
    parser.add_argument('-nb', '--no-branches', action='store_true', help='不获取分支信息，加快处理速度')
    parser.add_argument('-c', '--check-rate', action='store_true', help='仅检查API速率限制状态，不执行其他操作')
    
    args = parser.parse_args()
    
    # 验证参数
    if not args.check_rate and not args.repo:
        parser.error("必须提供仓库路径参数，除非使用 --check-rate 选项")
    
    return args

def main():
    args = parse_arguments()
    
    # 如果只是检查API速率限制
    if args.check_rate:
        g = Github(load_github_token())
        remaining, reset_time = check_api_rate_limit(g)
        logger.info(f"\n=== GitHub API 速率限制状态 ===")
        logger.info(f"剩余请求次数: {remaining}")
        logger.info(f"重置时间: {reset_time.strftime('%Y-%m-%d %H:%M:%S')} (上海时间)")
        
        # 计算距离重置还有多长时间
        now = datetime.now(timezone.utc).astimezone(shanghai_tz)
        time_to_reset = (reset_time - now).total_seconds()
        hours, remainder = divmod(time_to_reset, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if time_to_reset > 0:
            logger.info(f"距离重置还有: {int(hours)}小时 {int(minutes)}分钟 {int(seconds)}秒")
        else:
            logger.info("API速率已重置")
            
        # 估算可以处理的fork数量
        if remaining > 0:
            # 假设每个fork平均消耗15个API请求
            estimated_forks = remaining // 15
            logger.info(f"估计可以处理约 {estimated_forks} 个fork (基于每个fork平均消耗15个API请求)")
        
        return
    
    # 如果不是只检查API速率，则需要处理仓库
    repo_path = args.repo
    repo = get_repository_info(repo_path)
    
    forks_info = get_forks_info(
        repo, 
        max_forks=args.max, 
        workers=args.workers,
        show_comparison=not args.no_compare,
        skip_no_diff=args.skip_no_diff,
        resume=args.resume,
        check_branches=not args.no_branches
    )
    
    print_forks_info(forks_info, top_n=args.top)
    
    # 如果指定了输出文件，则保存结果
    if args.output:
        save_to_file(forks_info, args.output)
    
    # 保存到数据库
    db_filename = f"{repo_path.replace('/', '_')}_forks.db"
    if args.output:
        base_name = os.path.splitext(args.output)[0]
        db_filename = f"{base_name}.db"
    
    save_to_sqlite(forks_info, db_filename)

if __name__ == "__main__":
    main()
