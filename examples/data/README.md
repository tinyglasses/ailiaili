# 不跑模型，也能使用这份结果

这里的独立数据示例需要 Python 3.11 或更新版本；完整模拟框架需要 Python 3.12 或更新版本。示例不需要 `pip install`、模型密钥或封面下载。以下命令都从仓库或独立数据包的根目录运行。Windows 可将 `python` 换为 `py -3`，先用 `py -3 --version` 确认版本。

前三个脚本默认读取本包的公开数据，将UTF-8 JSON打印到终端；第四个脚本读取明确指定的两个世界，默认输出Markdown对照。`--output`可保存文件，不依赖终端重定向的编码；脚本拒绝把输出写回本包的`data/`。

## 1. 读一个 UP 主的作品，带走一个简洁 JSON

```sh
python examples/data/creator_works.py --creator creator_050 --output outputs/daily-epic.json
```

这会导出“把日常拍成史诗”的全部作品：按模拟日排序，只保留标题、核心设定、提纲和封面描述。也可用准确昵称代替 ID。

本包应返回 `work_count: 21`。

只看 Day 20 之前发布、提纲中含某个词的作品：

```sh
python examples/data/creator_works.py --creator creator_050 --through-day 20 --query "洗衣"
```

`through-day` 包含当天。词语会匹配标题、设定和提纲，不会搜索图片内容；没有命中会正常返回空列表。

## 2. 某天各 UP 主得到多少曝光，点开率是多少

```sh
python examples/data/creator_day_metrics.py --day 60 --output outputs/day60-creators.json
```

输出每位创作者的**当天**曝光、点开、新增关注、三连，以及点开率。数据按创作者 ID 排列，不做模型能力排名。

本包的 Day 60 有 100 位作者，合计曝光 101,400、点开 7,299，点开率约 7.20%。

计算整个时期：

```sh
python examples/data/creator_day_metrics.py --from-day 1 --day 60 --output outputs/whole-run-creators.json
```

脚本只相加 `entity_type=creator` 的行。总点开率是 **总点开 / 总曝光**，不会平均 100 个作者的百分比；曝光为 0 时为 `null`。`days_with_rows` 告诉你每个作者在区间内实际有多少日记录，缺行不直接补零。

这里的曝光和点开来自模拟观众，包括参考观众；不是网站访问统计，也不是成片播放量。计算关注率时还需要关注机会这个分母，请看[数据字典](../../docs/DATA.md)。

## 3. 比较一个 UP 主在两天之间的变化

```sh
python examples/data/creator_change.py --creator creator_050 --from-day 20 --to-day 60 --output outputs/epic-day20-to60.json
```

读取 JSON 的截至 Day 20 与截至 Day 60 累计数，再相减。`change` 是 **Day 21—60** 的新增行为和作品数，`interval_ctr` 为这段时间的点开 / 曝光。不会用两个累计点开率相减来代替区间点开率。

本例区间新增 14 份作品，曝光 47,020、点开 4,380；可用第二个脚本汇总 Day 21—60 后交叉核对。

这两个日期都可以查指标；但公开首页快照只有 Day 1、59、60，不能据此声称拥有 Day 20 的首页原貌。

## 4. 比较两个世界

```console
python examples/data/compare_worlds.py --left my-results-a/world.json --right my-results-b/world.json --output outputs/comparison.md
```

两个输入都须有完整的作者累计序列。默认取双方都完成的最后一个共同日，也可用`--day 5`指定；`--format json`可输出结构化结果。报告包含作品数、总曝光、总点开、加权点开率、曝光最多作者占比和零曝光作者数。缺少共同日期或完整累计序列会报错，已有输出文件不会覆盖。

新run导出的`experiment_settings`存在时，报告会列出公开设置的差异；它不是全部模型、观众、代码和服务状态的完整证明，不能由一次对照直接推出因果或能力排名。

## 换自己的数据与扩展代码

- `creator_works.py`：改 `selected.append(...)` 的字段列表，即可追加 `creator_note`、`inspiration` 等列；修改 `searchable` 可扩大检索范围。
- `creator_day_metrics.py`：先保留同一统计视角，再分组；不要混加作者、作品与平台行。想分析分区，可另做一个脚本只筛选 `category`。
- `creator_change.py`：按 `metrics.columns` 取列，避免将来列顺序变化导致误读；分母为 0 时保留 `null`。

兼容的浏览投影可传入 `--world my-results/world.json`。`creator_change.py` 还要求包含按 Day 索引的累计 `community.metrics`。模拟框架的其他报告、数据库快照不是这个格式，不承诺能直接替换。

如果自己的指标 CSV 具有相同每日字段，但没有浏览投影，可单独分析：

```sh
python examples/data/creator_day_metrics.py --metrics my-results/daily_metrics.csv --no-world --run-id my-experiment --day 3
```

仅当 CSV 内有且只有一个运行 ID 时，`--run-id` 才能省略。脚本对重复作者/日期、缺少请求日和错配的运行 ID 报错，避免得到看似有效的混合结果。

这些示例不改变模拟规则，不运行 API，也不能将公开结果继续模拟。完整数据边界见 [DATA.md](../../docs/DATA.md)，复用许可见 [RIGHTS.md](../../RIGHTS.md)。
