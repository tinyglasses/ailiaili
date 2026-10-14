# 扩展与对照实验

先运行[最小世界](QUICKSTART.md)，再选择一个具体问题。已有工具支持改配置、改人设、改模型提示和改源码；它不是任意插件都能热加载的通用Agent平台。

如果只想快速改模型、性格、分区、账号或观众，优先使用[简版配置](CUSTOMIZE.md)；本页介绍更细的控制和源码扩展。

## 先试推荐参数

```console
python -m ailiaili run --config configs/examples/recommend-baseline.yaml
python -m ailiaili run --config configs/examples/recommend-explore.yaml
python tools/serve.py --directory runs/recommend-baseline/site --port 8770
```

停止服务后，把`--directory`换成`runs/recommend-explore/site`即可浏览第二组；也可另开终端使用不同端口。

两组使用相同seed、种群、作品初始量、7天日程和stub行为。除run标识外，只把`exploit_ratio / cold_ratio / explore_ratio`从`0.8 / 0.1 / 0.1`改为`0.4 / 0.1 / 0.5`。10卡首页的名义全局分配从`8 / 1 / 1`变为`4 / 1 / 5`；候选不足、去重和作者上限可能触发补位，实际卡片以首页记录为准。

可以比较内容曝光分布、哪些作品进入首页、点击/关注随天数的变化。这个示例检验的是软件中的规则与stub响应，不能据此声称探索推荐改善了真实用户体验。两个独立新run也不是“同一状态分叉”；若要共享某天之前的状态，应使用世界快照。

## 从同一状态分两条路

以下命令接续已完成的`minimal` Day5快照。两条分支均从同一快照出发，各推进三天：

```console
python -m ailiaili world-fork --snapshot runs/snapshots/minimal-day5.ailiaili-world.zip --run-id fork-control --additional-days 3 --purpose "Unchanged control" --output-root runs
python -m ailiaili world-fork --snapshot runs/snapshots/minimal-day5.ailiaili-world.zip --run-id fork-explore --additional-days 3 --purpose "Increase exploration" --output-root runs --set exploit_ratio=0.25 --set explore_ratio=0.5
python -m ailiaili run --config runs/fork-control/branch.yaml
python -m ailiaili run --config runs/fork-explore/branch.yaml
python -m ailiaili report --config runs/fork-control/branch.yaml
python -m ailiaili report --config runs/fork-explore/branch.yaml
```

此处保留`cold_ratio=0.25`，三项总和仍为1。`--set`只接受`world_branch.py`中的白名单，支持推荐比例、老化参数、部分反馈激活概率、并发和记录策略等；不能借此换种群、角色身份、模型或运行模式。分支记录父run、父日、用途、覆盖项及续跑代码hash。创建分支后改变其行为代码或外部依赖会被拒绝；需要改行为源码时，应明确新实验边界，而非手工删除hash绕过验证。

## 修改入口

| 想改变什么 | 最小入口 | 层级与限制 |
| --- | --- | --- |
| 种群、天数、首页大小、发稿周期 | 新YAML中的`creator_count/viewer_count/days/homepage_size/publish_interval_days` | 配置；换新run，避免改已冻结run |
| 全局探索、冷启动、内容老化 | `exploit_ratio/cold_ratio/explore_ratio/cold_impression_target/age_half_life_days` | 配置；比例和为1 |
| 兴趣与关注候选位 | `interest_slots_max/following_slots_max` | 配置；两者和不超过首页大小，不保证每次填满 |
| 评分公式、抽样和补位规则 | `src/ailiaili/recommendation.py` | 源码；关注`smoothed_performance`、`recommendation_weight`与`Recommender.sample` |
| stub作者身份、分区、初始题材 | 简版配置或`stub_creators` | 配置；模板措辞和选择算法仍在`src/ailiaili/stubs.py`，stub不会演绎自然语言性格 |
| LLM创作者和观众人设 | 配置的`creator_persona_path/viewer_persona_path`；参考`configs/reference/` | 配置与数据；`persona.py`负责加载/校验，不是自由文本替换所有行为 |
| LLM输入与输出协议 | `creator/prompt.py`、`viewer/prompt.py`、`domain.py` | 源码；修改字段还需同步校验、服务和持久化 |
| 调用模型、价格、密钥变量、请求节奏 | 简版配置或`llm_runtime_path`指向的模型YAML | 支持OpenRouter、Volcengine Ark、通用Chat Completions接口；其他协议需适配 |
| 新供应商、调用日志、预算实现 | `llm/provider.py`、`llm/gateway.py` | 源码；保留调用与尝试的区别及费用口径 |
| 观看后兴趣、关注与三连转换 | `viewer/behavior.py`、`viewer/service.py` | 源码及LLM运行的`follow_activation_* / triple_activation_*`概率配置；不代表真实平台行为 |
| 新指标、数据库字段 | `persistence/models.py`、`simulation/runner.py`、`reports/exporter.py` | 源码；无通用数据库迁移命令，勿直接拿改后schema续写旧库 |
| 新run的观察报告 | `reports/exporter.py`、`reports/templates/index.html` | 报告；不等于自动重建正式成片页面 |

配置还会检查：`homepage_size <= creator_count * max_videos_per_creator`；LLM模式每位作者Day0恰有一份初始作品；模型别名、人设和运行配置必须完整。LLM的`max_clicks`还须与观众人设里最大的`click_cap`一致，当前参考人设为3，即使小世界的首页只有1—2张卡也保留该值。不要假设拼错的YAML键会改变行为，核对`SimulationConfig`和落盘的`manifest.json`。

## 从一个UP主灵感开始

可以从已发布角色中复制人设，作为自己新账号的起点：

```console
python tools/prepare_creator.py --creator creator_038 --new-id creator_901 --name "NPC下班以后" --output configs/local/my-creators.yaml
```

这个工具只复制、调整并校验单角色配置，不调用模型。编辑输出的人设后，以LLM小世界模板为起点，至少修改以下字段，并使用新的`run_id`：

```yaml
run_id: my-npc-world
creator_count: 1
creator_ids: [creator_901]
creator_persona_path: configs/local/my-creators.yaml
homepage_size: 1
max_videos_per_creator: 1
```

还需保留观众人设配置、填写模型路由与预算，并设置自己的密钥。单账号首页也要缩小，不能照搬多账号示例的容量。`configs/local/`用于本机实验，不应放密钥；作品文本、人设和图片的再使用仍须遵守项目的[授权说明](../RIGHTS.md)。stub只检查本地模拟机制，不会演绎这个新角色的真实模型创作行为。

## 接入自己的模型

提供两个**故意未填写完整**的模板：

- [llm-small.template.yaml](../configs/examples/llm-small.template.yaml)：两位创作者、四位观众、一天的新世界。
- [llm-runtime.template.yaml](../configs/examples/llm-runtime.template.yaml)：模型服务与小额预算。模型ID是占位符，价格为`null`，会在验证阶段拒绝运行。

填写当前有效模型ID、价格和真实支持的JSON模式；价格使用所选币种的每百万token金额。不要把参考配置中的历史价格当成当前报价。小模板用现有参考人设，实际内容仍会受模型和抽样影响。

代码读取进程环境变量，不会自动加载`.env`。在本机设置模板指定的`AILIAILI_MODEL_API_KEY`，不要把密钥写入配置或提交到仓库。例如，当前终端中设置：

```powershell
$env:AILIAILI_MODEL_API_KEY = "在本机填写自己的密钥"
```

```bash
export AILIAILI_MODEL_API_KEY='在本机填写自己的密钥'
```

完成模板后，以下命令会真正调用模型并产生供应商费用：

```console
python -m ailiaili run --config configs/examples/llm-small.template.yaml
```

`budget.hard_limit`与`max_cost_per_call`控制本run的累计和单次费用预留；混合币种可用`hard_limit_by_currency/max_cost_per_call_by_currency`分别设USD、CNY上限，不自动换汇。网关先按配置价格和token估计预留，再按返回的费用或用量更新。价格过时、用量缺失、超时的外部计费等都会影响准确性：**这是本地运行护栏，不是供应商账单保证，也不会读取ChatGPT订阅额度。**

`max_attempts_per_call`限制常规技术重试；显式重试授权可能开新一轮。`max_concurrency`控制全局并发，模型配置的`max_in_flight`限制该路由并发，`request_start_interval_ms/request_start_batch_size`控制启动节奏。限额拒绝、服务限流与失败都会保留记录；这些设置不用于绕过服务限制。
