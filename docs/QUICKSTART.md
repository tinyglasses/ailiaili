# 运行第一个世界

这个入口面向想运行模拟器的人。只想浏览已发布作品，可使用项目首页的展示入口。下面的示例全部使用本地 **stub**：规则和固定模板代替模型，既不需要密钥，也不产生模型费用。它们用于理解软件行为，不代表AI模型实验结果。

想从自己命名的分区和角色开始，安装后可直接走[一份配置自定义](CUSTOMIZE.md)，无需先手写完整人设文件。

## 安装

需要 Python 3.12 或更高版本。从项目根目录执行命令；配置中的相对路径以当前工作目录为准。

Windows PowerShell：

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]" -c requirements-tested.txt
```

macOS / Linux：

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]' -c requirements-tested.txt
```

`requirements-tested.txt`固定本次测试使用的依赖版本。若在不同Python版本或平台上遇到兼容问题，可去掉`-c requirements-tested.txt`使用`pyproject.toml`支持的版本范围，并重新运行测试。

后文的`python`均指这个虚拟环境：PowerShell可直接替换为`.\.venv\Scripts\python.exe`，macOS/Linux替换为`.venv/bin/python`。也可先激活环境：PowerShell执行`.\.venv\Scripts\Activate.ps1`，macOS/Linux执行`source .venv/bin/activate`。激活受限时使用完整解释器路径即可，不必修改系统执行策略。

## 最小世界：先跑两天，再继续

```console
python -m ailiaili run --config configs/examples/minimal.yaml --days 2
python -m ailiaili status --config configs/examples/minimal.yaml
python -m ailiaili run --config configs/examples/minimal.yaml --days 5
python -m ailiaili report --config configs/examples/minimal.yaml
python tools/serve.py --directory runs/minimal/site --port 8770
```

打开`http://127.0.0.1:8770/`，按`Ctrl+C`停止本地网页服务。配置定义3个创作者、6个观众和5天上限；首次调用完成Day1—2，第二次继续完成Day3—5。`--days 5`表示“运行到Day5”，不是“再运行5天”。重复运行已完成目标不会重建这些天。

输出在`runs/minimal/`：

| 文件或目录 | 用途 |
| --- | --- |
| `ailiaili.db` | 这个新世界的SQLite事实与状态 |
| `manifest.json` | 已冻结的运行配置 |
| `site/index.html` | 此次运行的观察报告，可浏览时间线、首页与指标 |
| `site/summary.json`、`site/world_timeline.json` | 报告使用的数据投影 |

新run生成的报告和仓库的`site/index.html`是两个入口：后者展示已发布的固定世界，具有成片使用的版式；运行模拟器不会自动把它替换成新世界，也不会自动生成PNG封面。

已有`configs/demo.yaml`是较大的7天stub示例：

```console
python -m ailiaili run --config configs/demo.yaml
python -m ailiaili status --config configs/demo.yaml
python -m ailiaili report --config configs/demo.yaml
python tools/serve.py --directory runs/demo/site --port 8770
```

运行核心测试：`python -m pytest`。命令清单：`python -m ailiaili --help`。

## 从头运行、恢复和分支的区别

| 想做的事 | 正确方式 |
| --- | --- |
| 重跑一个新世界 | 复制YAML，换未使用的`run_id`后运行；保留旧run |
| 继续本机已有世界 | 使用原YAML、原数据库，再执行`run`，从未完成处继续 |
| 在原计划内多跑几天 | 同一配置使用更大的`--days`，不能超过配置的`days` |
| 超过原计划，或从同一状态试规则变化 | 创建世界快照，再`world-fork`为新run |
| 接着公开的2003份作品继续演化 | 本次公开投影不具备所需完整状态，不能直接续跑 |

同一个`run_id`对应的配置会与数据库中的配置比对。直接修改`days`、种群或推荐规则再运行原run，会报配置不同；这不是修改成功。`world-fork`是模拟世界分支，与GitHub的仓库fork不同。

## 在Day5之后继续三天

先完成上述`minimal`的Day5，然后执行：

```console
python -m ailiaili world-snapshot --source-run-dir runs/minimal --day 5 --output runs/snapshots/minimal-day5.ailiaili-world.zip
python -m ailiaili world-fork --snapshot runs/snapshots/minimal-day5.ailiaili-world.zip --run-id minimal-next --additional-days 3 --purpose "Continue the example for three days" --output-root runs
python -m ailiaili run --config runs/minimal-next/branch.yaml
python -m ailiaili report --config runs/minimal-next/branch.yaml
python tools/serve.py --directory runs/minimal-next/site --port 8770
```

分支继承截至Day5的可续跑状态，独立生成Day6—8；原世界保持原样。分支标记为非canonical，默认不自动导出报告，所以这里显式执行`report`。快照路径和分支目录必须是新的，重复演示时请换名字。

## 中断后怎么办

stub运行中断后，确认旧进程已经退出，使用同一YAML再次`run`。其已有结果会被复用。不要同时启动两个进程修改同一run。

下面两个恢复命令**仅用于LLM运行**，不是stub必做步骤，也不会把公开投影变成可续跑数据库：

- `retry-incomplete-day`：仅为尚未完成的一天重新授权技术失败任务；保留成功调用和尝试记录。修复上游或格式问题后，执行该命令，再执行原`run`。已完成日不会因此重算。
- `recover-interrupted-day --confirm-process-stopped`：处理硬中断后状态仍为running、无法确认回执的请求。必须确认原进程已不可能返回；命令将不确定尝试标为放弃并允许后续重试，保留保守费用预留，不能保证供应商未计费。

```console
python -m ailiaili retry-incomplete-day --config path/to/your-llm-world.yaml
python -m ailiaili recover-interrupted-day --config path/to/your-llm-world.yaml --confirm-process-stopped
```

按实际故障选择一种，不要把两条命令当成每次启动的固定流程。详情与扩展示例见[EXTENDING.md](EXTENDING.md)，模块关系见[ARCHITECTURE.md](ARCHITECTURE.md)。
