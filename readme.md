# GitHub Forks 更新检查器

这个工具可以帮助你检查GitHub项目的fork情况，找出最近更新的、可能包含有价值改进的fork。

## 功能特点

- 自动获取指定GitHub仓库的所有fork
- 分析fork的更新时间、commit数量等信息
- 按照最近更新时间排序
- 显示fork的详细信息（star数、fork数等）
- 自动检测并处理API速率限制
- 自动跳过不存在的fork
- 支持多分支对比分析
- 智能等待API限制重置
- 断点续传功能，支持中断后继续执行
- 支持仅检查API速率限制状态
- 可选择是否获取分支信息，优化API使用

## 使用方法

1. 首先安装依赖：
```bash
pip install -r requirements.txt
```

2. 创建`.env`文件并添加你的GitHub Token：
```
GITHUB_TOKEN=your_token_here
```

3. 运行脚本：
```bash
python main.py <owner>/<repo>
```

例如：
```bash
python main.py microsoft/vscode
```

## 获取GitHub Token

1. 访问 https://github.com/settings/tokens
2. 点击 "Generate new token"
3. 选择 "Generate new token (classic)"
4. 在 "Note" 字段中输入 "Fork Checker"
5. 在 "Select scopes" 中至少选择 "repo" 权限
6. 生成并复制token
7. 将token粘贴到`.env`文件中

## 注意事项

- 程序会自动处理GitHub API的使用限制
- 当API使用次数即将耗尽时，会自动等待重置
- 对于已删除或无法访问的fork会自动跳过
- 对于大型仓库，获取所有fork信息可能需要一些时间
- Token请妥善保管，不要泄露 

## 高级使用方法

这个工具提供了多种参数来处理和分析fork，以下是一些高级使用方法：

### 参数说明

| 参数 | 描述 |
| ------ | ------ |
| `-m, --max` | 最多处理的fork数量 |
| `-w, --workers` | 并行处理的线程数（默认10） |
| `-t, --top` | 获取前N个结果 |
| `-o, --output` | 将结果保存到指定文件 |
| `-nc, --no-compare` | 不获取与源仓库的比较数据（加快速度） |
| `-s, --skip-no-diff` | 跳过与源仓库无差异的fork（快速通过） |
| `-r, --resume` | 从上次中断的地方继续执行（断点续传） |
| `-nb, --no-branches` | 不获取分支信息，加快处理速度 |
| `-c, --check-rate` | 仅检查API速率限制状态，不执行其他操作 |

### 使用示例

1. 处理所有fork，使用20个线程：
```bash
python main.py owner/repo -w 20
```

2. 处理前100个fork，跳过与源仓库无差异的fork：
```bash
python main.py owner/repo -m 100 -s
```

3. 快速通过模式 - 处理前50个fork，不获取比较数据：
```bash
python main.py owner/repo -m 50 -nc
```

4. 处理所有fork，获取前20个fork，并将结果保存到文件：
```bash
python main.py owner/repo -t 20 -o results.md
```

5. 完整模式 - 跳过与源仓库无差异的fork，使用30个线程，获取前10个fork，并将结果保存到文件：
```bash
python main.py owner/repo -s -w 30 -t 10 -o best_forks.md
```

6. 仅检查API速率限制状态：
```bash
python main.py -c
```

7. 从上次中断的地方继续执行：
```bash
python main.py owner/repo -r
```

8. 不获取分支信息，加快处理速度：
```bash
python main.py owner/repo -nb
```

## 输出信息

程序会显示以下信息：

- API速率限制状态和重置时间
- 仓库基本信息
- 处理进度和统计
- 每个fork的详细信息：
  - 仓库名称和链接
  - Star数和Fork数
  - 最近更新时间
  - 所有分支列表
  - 每个分支与原仓库的对比数据
  - 仓库描述

## 断点续传功能

当程序因为以下原因中断时，会自动保存当前进度：
- API速率限制耗尽
- 用户手动中断（Ctrl+C）
- 程序异常退出

您可以使用 `-r` 或 `--resume` 参数从上次中断的地方继续执行：
```bash
python main.py owner/repo -r
```

进度文件保存在当前目录下，文件名格式为 `owner_repo_progress.json`。

## 常见问题

1. **处理速度优化**
   - 使用 `-nc` 参数跳过比较数据获取
   - 使用 `-s` 参数跳过与源仓库无差异的fork
   - 使用 `-w` 参数增加并行处理线程数
   - 使用 `-m` 参数限制处理fork数量
   - 使用 `-nb` 参数跳过分支信息获取

2. **不存在的fork**
   - 程序会自动检测并跳过不存在的fork
   - 在最终统计中会显示跳过的数量

3. **分支对比**
   - 默认会对所有分支进行对比
   - 如果某个分支无法对比，会自动跳过并继续处理其他分支
   - 使用 `-nc` 参数可以完全跳过分支对比
   - 使用 `-nb` 参数可以跳过获取分支信息，只比较默认分支

4. **API速率限制**
   - 使用 `-c` 参数可以快速检查当前API速率限制状态
   - 程序会自动等待API速率重置
   - 当API速率即将耗尽时，会自动保存进度并提示使用 `-r` 参数继续执行
