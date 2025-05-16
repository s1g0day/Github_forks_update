import os
import sys
import time
import argparse
import json
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

# 进度计数器锁
progress_lock = threading.Lock()
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
        rate_limit = g.get_rate_limit()
        core_rate = rate_limit.core
        remaining = core_rate.remaining
        reset_time = core_rate.reset
        # 将reset_time转换为上海时区
        reset_time_shanghai = reset_time.astimezone(shanghai_tz)
 
        if remaining < 100 or force_wait:  # 如果剩余次数少于100或强制等待
            # 创建带时区的UTC当前时间
            utc_now = datetime.now(timezone.utc)
            wait_time = (reset_time - utc_now).total_seconds()
            if wait_time > 0:
                message = f"⚠️ API速率即将耗尽（剩余{remaining}次）"
                wait_with_progress(wait_time + 1, message)  # 额外等待1秒以确保重置完成
                return check_api_rate_limit(g)  # 重新检查速率
        
        return remaining, reset_time_shanghai
    except Exception as e:
        print(f"\n检查API速率限制时出错: {str(e)}")
        # 如果无法获取速率限制信息，保守等待一段时间
        wait_with_progress(300, "无法获取API速率限制信息，保守等待")
        return 0, datetime.utcnow()

def retry_with_backoff(func, *args, max_attempts: int = 5, **kwargs):
    """使用指数退避的重试机制"""
    global rate_limit_count
    
    for attempt in range(max_attempts):
        try:
            return func(*args, **kwargs)
        except RateLimitExceededException:
            with progress_lock:
                rate_limit_count += 1
                print(f"\n⚠️ 第 {attempt + 1} 次尝试触发API限制...")
            if attempt < max_attempts - 1:
                check_api_rate_limit(args[0], force_wait=True)  # 第一个参数应该是Github实例
        except GithubException as e:
            if e.status == 403:  # Forbidden
                delay = exponential_backoff(attempt)
                with progress_lock:
                    print(f"\n⚠️ 请求被拒绝（403），等待 {delay:.1f} 秒后重试...")
                wait_with_progress(delay, "等待重试")
            else:
                raise
        except Exception as e:
            if attempt == max_attempts - 1:
                raise
            delay = exponential_backoff(attempt)
            with progress_lock:
                print(f"\n⚠️ 发生错误: {str(e)}，{delay:.1f} 秒后重试...")
            wait_with_progress(delay, "等待重试")
    
    raise Exception(f"在 {max_attempts} 次尝试后仍然失败")

def get_commits_safely(repo, **kwargs):
    """安全地获取提交信息"""
    try:
        return retry_with_backoff(lambda: repo.get_commits(**kwargs).get_page(0))
    except Exception as e:
        print(f"\n警告：无法获取提交信息 - {str(e)}")
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
        print(f"检查仓库时发生错误: {str(e)}")
        return None

def load_github_token() -> str:
    """加载GitHub Token"""
    load_dotenv()
    token = os.getenv('GITHUB_TOKEN')
    if not token:
        print("错误：未找到GITHUB_TOKEN。请在.env文件中设置您的GitHub Token。")
        sys.exit(1)
    return token

def get_repository_info(repo_path: str) -> Repository:
    """获取仓库信息"""
    try:
        g = Github(load_github_token())
        # 检查API速率限制
        remaining, reset_time = check_api_rate_limit(g)
        print(f"当前API速率限制状态：剩余 {remaining} 次，将于上海时间 {reset_time.strftime('%Y-%m-%d %H:%M:%S')} 重置")
        
        # 检查仓库是否存在
        repo = check_repository_exists(g, repo_path)
        if not repo:
            print(f"错误：仓库 {repo_path} 不存在或无法访问")
            sys.exit(1)
        return repo
    except Exception as e:
        print(f"错误：无法获取仓库信息 - {str(e)}")
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
        print(f"\n✅ 进度已保存到文件: {progress_file}")
    except Exception as e:
        print(f"\n❌ 保存进度时出错: {str(e)}")

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
            print(f"\n⚠️ 进度文件不匹配当前仓库，将重新开始")
            return [], set()
        
        forks_info = data.get('forks_info', [])
        processed_fork_names = set(data.get('processed_fork_names', []))
        timestamp = data.get('timestamp', '未知时间')
        
        print(f"\n✅ 已加载之前的进度 (保存于 {timestamp})")
        print(f"已处理 {len(processed_fork_names)} 个fork，已收集 {len(forks_info)} 个结果")
        
        return forks_info, processed_fork_names
    except Exception as e:
        print(f"\n⚠️ 加载进度时出错: {str(e)}，将重新开始")
        return [], set()

def process_fork(g: Github, repo: Repository, fork, total_forks: int, show_comparison: bool, skip_no_diff: bool, check_branches: bool = True) -> Dict:
    """处理单个fork的信息，返回处理结果"""
    global processed_count, error_count, skipped_count, nonexistent_count, processed_forks
    
    # 检查是否已经处理过这个fork
    if fork.full_name in processed_forks:
        with progress_lock:
            print(f"\r已跳过: {fork.full_name} (之前已处理)")
        return None
    
    try:
        # 检查fork是否仍然存在
        fork_repo = retry_with_backoff(check_repository_exists, g, fork.full_name)
        if not fork_repo:
            with progress_lock:
                nonexistent_count += 1
                processed_count += 1
                processed_forks.add(fork.full_name)  # 添加到已处理集合
                print(f"\r处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork.full_name} (仓库不存在，跳过)")
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
                                        print(f"\r处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork_repo.full_name} (无差异，跳过)")
                                    return None
                        except Exception as e:
                            print(f"\n警告：无法比较分支 {branch.name} - {str(e)}")
                            continue
            except Exception as e:
                print(f"\n警告：无法获取分支信息 - {str(e)}")
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
                            print(f"\r处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork_repo.full_name} (无差异，跳过)")
                        return None
                except Exception as e:
                    print(f"\n警告：无法比较默认分支 - {str(e)}")
    
        with progress_lock:
            processed_count += 1
            processed_forks.add(fork.full_name)  # 添加到已处理集合
            progress = f"\r处理中: {processed_count}/{total_forks} [{processed_count/total_forks*100:.1f}%] - {fork_repo.full_name}"
            if fork_info['ahead_by'] is not None:
                progress += f" (领先: {fork_info['ahead_by']}, 落后: {fork_info['behind_by']})"
            print(progress, end="", flush=True)
            
        return fork_info
    except Exception as e:
        with progress_lock:
            error_count += 1
            processed_count += 1
            processed_forks.add(fork.full_name)  # 添加到已处理集合
            print(f"\n警告：处理 {fork.full_name} 时出错 - {str(e)}")
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
    
    print(f"\n正在获取 {repo.full_name} 的fork信息...")
    
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
                                print("\n\n⚠️ API速率即将耗尽，正在保存进度...")
                                save_progress(repo.full_name, forks_info, processed_forks)
                                print("进度已保存，请稍后使用 --resume 参数继续执行")
                                print(f"建议等待API速率重置后再继续")
                                return forks_info
                            
                    except Exception as e:
                        print(f"\n错误：处理fork时发生异常 - {str(e)}")
        finally:
            # 恢复原始信号处理器
            if sys.platform != 'win32' and original_sigint_handler:
                signal.signal(signal.SIGINT, original_sigint_handler)
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n全部处理完成！成功获取了 {len(forks_info)}/{total_forks} 个fork的信息")
        if nonexistent_count > 0:
            print(f"跳过了 {nonexistent_count} 个不存在的fork")
        if skipped_count > 0:
            print(f"跳过了 {skipped_count} 个无差异的fork")
        if rate_limit_count > 0:
            print(f"遇到 {rate_limit_count} 次API限制")
        print(f"总耗时: {duration:.2f}秒, 平均每个fork耗时: {duration/total_forks:.2f}秒")
        if error_count > 0:
            print(f"处理失败: {error_count}个")
            
        # 最后保存一次进度
        save_progress(repo.full_name, forks_info, processed_forks)
            
    except Exception as e:
        print(f"错误：获取fork列表时出错 - {str(e)}")
        # 发生错误时也保存进度
        save_progress(repo.full_name, forks_info, processed_forks)
        sys.exit(1)
        
    return forks_info

def print_forks_info(forks_info: List[Dict], top_n: int = None):
    """打印fork信息，可以限制只显示前N个"""
    if not forks_info:
        print("\n未找到符合条件的fork")
        return
    
    # 按最后更新时间排序
    sorted_forks = sorted(forks_info, key=lambda x: x['last_updated'], reverse=True)
    
    # 如果指定了top_n，则只显示前N个
    if top_n and top_n < len(sorted_forks):
        display_forks = sorted_forks[:top_n]
        print(f"\n=== Fork 分析结果 (显示前 {top_n} 个) ===")
    else:
        display_forks = sorted_forks
        print(f"\n=== Fork 分析结果 ===")
    
    print(f"共 {len(sorted_forks)} 个fork，显示 {len(display_forks)} 个\n")
    
    for fork in display_forks:
        print(f"仓库: {fork['name']}")
        print(f"链接: {fork['url']}")
        print(f"Stars: {fork['stars']}")
        print(f"Forks: {fork['forks']}")
        print(f"最后更新: {fork['last_updated'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"分支: {', '.join(fork['branches'])}")
        
        # 显示每个分支的比较结果
        if fork['branch_comparisons']:
            print("分支对比:")
            for branch, comparison in fork['branch_comparisons'].items():
                print(f"  - {branch}: 领先 {comparison['ahead_by']} commits, 落后 {comparison['behind_by']} commits")
        elif fork['ahead_by'] is not None and fork['behind_by'] is not None:
            print(f"默认分支对比: 领先 {fork['ahead_by']} commits, 落后 {fork['behind_by']} commits")
            
        print(f"描述: {fork['description']}")
        print("-" * 50)

def save_to_file(forks_info: List[Dict], filename: str):
    """将fork信息保存到文件"""
    if not forks_info:
        print("没有数据可保存")
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
                
        print(f"\n结果已保存到文件: {filename}")
    except Exception as e:
        print(f"保存文件时出错: {str(e)}")

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
        print(f"\n=== GitHub API 速率限制状态 ===")
        print(f"剩余请求次数: {remaining}")
        print(f"重置时间: {reset_time.strftime('%Y-%m-%d %H:%M:%S')} (上海时间)")
        
        # 计算距离重置还有多长时间
        now = datetime.now(timezone.utc).astimezone(shanghai_tz)
        time_to_reset = (reset_time - now).total_seconds()
        hours, remainder = divmod(time_to_reset, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if time_to_reset > 0:
            print(f"距离重置还有: {int(hours)}小时 {int(minutes)}分钟 {int(seconds)}秒")
        else:
            print("API速率已重置")
            
        # 估算可以处理的fork数量
        if remaining > 0:
            # 假设每个fork平均消耗15个API请求
            estimated_forks = remaining // 15
            print(f"估计可以处理约 {estimated_forks} 个fork (基于每个fork平均消耗15个API请求)")
        
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

if __name__ == "__main__":
    main()