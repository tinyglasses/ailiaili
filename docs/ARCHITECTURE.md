# 模拟框架结构

AiliAili运行一个离散的内容社区世界：创作者定期发布结构化作品，推荐器生成首页，观众选择内容并给出反馈，状态与指标随模拟日推进。它不自动制作视频，也不实时服务真实用户。

静态网站中的动态浏览在读者浏览器内重用推荐规则：公开逐日数据 → 指定日期的可见目录与反馈 → 新的40卡首页。它与历史会话回放并存，不向模拟器写入读者行为，也不需要服务端或模型调用。抽样函数、页面连接和输入导出分别位于`site/recommendation-core.js`、`site/recommendation-ui.js`与`tools/build_recommendation_data.py`。

```text
YAML 配置 + 人设 + 模型路由（LLM模式）
                  │
              CLI / runner
                  │
    创作者发稿 → 作品目录 → 推荐首页 → 观众选择和反馈
         ↑                              │
         └──────── 下一天的状态 ─────────┘
                  │
       SQLite事实、状态、调用与尝试记录
                  │
      新run观察报告 / 可分析的数据投影
```

## 代码职责

| 模块 | 负责什么 |
| --- | --- |
| `cli.py`、`config.py` | 命令入口、配置模型与基本约束 |
| `simple_config.py`、`experiment_setup.py`、`presets/` | 简版配置、创作方式、三个起点与离线工作量预览 |
| `simulation/runner.py` | Day0初始化、日循环、幂等恢复、完成门槛及落盘 |
| `stubs.py` | 无模型的作者/观众模板与确定性行为 |
| `persona.py` | LLM种群与人设配置加载 |
| `creator/` | 创作观察、提示词、结构化作品与创作者记忆 |
| `recommendation.py` | 表现、冷启动、探索与有限兴趣/关注候选位 |
| `viewer/` | 首页选择、点开后评价及反馈动作转换 |
| `llm/provider.py`、`llm/gateway.py` | 供应商请求、输出验证、逻辑调用/尝试日志、预算预留与重试 |
| `persistence/` | SQLite连接和SQLAlchemy实体 |
| `world_branch.py`、`continuation_contract.py` | 最小续跑快照、世界分支及续跑依赖约束 |
| `reports/` | 从运行事实导出观察报告和报告数据 |

## 两种运行方式

`execution_mode: stub`使用本地模板与种子驱动规则，适合检查日循环、推荐、存储、恢复和报告。它不模拟真实模型能力，也不会使用LLM的完整人设与调用流程。

`execution_mode: llm`使用人设、模型路由和提示词调用外部模型。一次逻辑任务可能经历多次技术尝试；成功、失败、用量和费用记录不是同一计数。`creator_count`与`viewer_count`只是规模的一部分，观众还可能点开多份内容，重试也会增加调用。

作品是标题、封面描述、简介、大纲等结构化内容。模型看到什么、平台实际记录了什么，应以对应调用与状态为准；展示PNG不是模拟器的自动产出。

## 状态和发布数据的边界

| 对象 | 可用于什么 | 不能替代什么 |
| --- | --- | --- |
| 自己新run的`ailiaili.db`及配置/依赖 | 恢复这个run、生成报告、创建续跑快照 | 已发布正式世界的原始数据库 |
| `world-snapshot`输出 | 从指定已完成日派生新世界 | 全部历史调用档案；快照有意只保留续跑所需状态和有限报告材料 |
| `data/world.json`与`data/daily_metrics.csv` | 浏览公开结果、读取作品与指标 | 完整角色状态、全部观众会话和逐次模型证据 |
| 仓库`site/index.html` | 浏览既定正式世界、按成片布局取景 | 任意新run的自动通用前端 |
| `runs/<run_id>/site/` | 浏览自己新运行产生的观察报告 | 为每份作品生成图片或成片 |

`world-fork`复制可续跑世界状态并记录来源；它不会复制完整父运行档案。分支默认使用compact证据模式，保留哪些信息由代码明确控制；若需要完整普通观众提示，可在创建时选`--full-evidence`。公开的作品投影不提供这类完整续跑能力。

已有run的配置被冻结。先在原计划上限内分段运行，才能用相同配置恢复；改变世界规则或超过上限时，应创建新run或合适的世界分支。分支也会校验外部配置文件与行为代码的hash，避免无记录地改变实验含义。

## 测试边界

`tests/`覆盖stub世界、推荐、观众行为、模型网关、健康门槛、运行进度与世界分支。LLM相关测试使用替身响应，不需要真实服务。测试通过说明这些软件契约成立，不能证明供应商模型永远可用，也不等于对公开作品的观点、事实或图片逐项认可。

建议从[QUICKSTART.md](QUICKSTART.md)运行，再按[EXTENDING.md](EXTENDING.md)选择配置层或源码层的修改入口。

## 把自己的run接到分析与阅读工具

配置入门层是`simple_config.py`和`ailiaili prepare`：一份小YAML先被严格校验，再展开为`SimulationConfig`、完整创作者人设、观众预设和模型运行配置。它不调用模型，也不改变既有世界；分区名称由配置定义，`presets/viewers.yaml`提供可替换的观众语法默认值。模型的通用接入遵循Chat Completions格式，复用既有预算、冷却和结果校验。

`python tools/export_run_data.py --run-dir runs/demo --output exports/demo` 只读本机数据库，导出已完成日的 `world.json` 与 `daily_metrics.csv`。生成的 `ailiaili-analysis-projection-v1` 包含 `community.catalog` 和可重建时的作者累计指标，可交给 `examples/data/` 与 `tools/export_inspiration.py --world ...`。

这个导出不会复制运行记忆、完整观众会话或请求日志；角色设定与作品本身仍需在分享前确认适合公开。它不包含成片页面所需的全部顶层示例、首页快照及视图状态，不能直接覆盖正式世界的 `data/world.json` 后重建成片版站点。新run的网页展示请先使用其 `runs/<run_id>/site/` 自动报告。
