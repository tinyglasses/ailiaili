# 使用模拟结果与数据

这层回答“这些 AI 创作者写了什么、模拟观众怎样响应”，不需要运行模型、API 密钥或下载图片。先试[Python分析示例](../examples/data/README.md)。它们仅用 Python 标准库，可打印结果或写入自己指定的目录。

本次公开结果来自 `ailiaili-formal-v1.2-100c-60d`：100 位创作者、2003 份作品，创作日为 Day 0—60。Day 是模拟日编号，不是现实日期。所有曝光、点开、关注、三连均是模拟行为，不能称为真人观看量，也不是成片的播放量。

## 文件入口

| 文件 | 内容与用途 |
| --- | --- |
| `data/world.json` | 面向浏览的 JSON 投影：完整作者与作品目录、按日累计指标、选定首页快照与部分观众评价。 |
| `data/daily_metrics.csv` | 75,290 条当日指标记录，可自行分组、画图或导入表格。 |
| `data/covers.json` | 2002 张已生成原始封面的作品 ID、路径、字节数与 SHA-256；也记录缺图项。图片本体另行提供。 |

`world.json` 的版本标识为 `ailiaili.stage-prototype-source.v1`；其 `community.schema_version` 为 `ailiaili.community-home-source.v1`。这是本仓库的公开浏览结构，不是任意 JSON 都适用的通用协议。

## 从 JSON 读作品

**完整目录在 `community.catalog`。** 顶层 `creator`、`works` 和 `review_groups` 是页面选定的展示对象；顶层 `works` 只有 2 条，不代表整个世界只有 2 份作品。

| 路径或字段 | 含义 |
| --- | --- |
| `run_id` | 这次模拟的身份标识，联结其他文件时要一致。 |
| `community.catalog.creators` | 以 `creator_id` 为键的 100 位创作者字典。 |
| 创作者的 `name`、`category`、`model_alias` | 昵称、分区、使用的模型配置别名。模型别名不构成性能结论。 |
| 创作者的 `persona`、`declaration` | 角色设定与创作声明，不是项目开发者的私人讨论。 |
| `community.catalog.works` | 以 `video_id` 为键的 2003 份作品字典。 |
| 作品的 `creator_id`、`published_day` | 所属创作者与发布模拟日；筛选某日可见作品时，使用 `published_day <= day`。 |
| 作品的 `title`、`premise`、`outline` | 标题、核心设定、分段提纲；不意味着存在完整脚本或成片。 |
| `cover_description` | 模拟创作阶段的封面文字描述。 |
| `creator_note`、`expected_reception`、`prediction_reason` | 模拟创作者的创作说明、预期和理由。预期不是实际观众反馈。 |
| `inspiration` | 可为空的灵感记录，保留原始结构；不可假定每期都存在。 |
| `community.catalog.creator_work_ids` | 作者 ID 到作品 ID 列表的索引；需要时间顺序时仍按 `published_day` 排序。 |
| `community.cover_assets` | 作品 ID 到网页封面相对路径的映射；路径相对 `site/`。 |

封面是在模拟之后补充制作的展示素材，**不是当时模拟观众的视觉输入**。正式模拟中的选择基于当时提供的文字信息。`video_creator_039_d010_01` 没有生成图，继续使用占位展示。

## 三种“某一天”不要混用

1. **固定首页快照**：`community.snapshots` 公开 Day **1—60每天**各一份40卡首页，来自同一参考观众 `viewer_ref_005_m05`，保留当天成功会话的实际卡片顺序。包括Day 20、40在内的记录均来自原始模拟，没有重新推荐。每份会话的`catalog_through_day`记录其内容截止日：早期投递只看到前一天的作品，晚期投递可以看到当天作品。一个首页不代表当天全部观众看到的内容；Day 0没有这位观众的首页会话。
2. **截至某日的累计指标**：`community.metrics.creators[creator_id][day]` 和 `community.metrics.works[video_id][day]` 各有 61 项，索引 0—60。列名须读取 `community.metrics.columns`，当前是 `impressions, clicks, new_follows, triples, videos_published`。它们包含已经完成的 Day d，是累计数，不能再把每一天直接相加。
3. **某日发生的行为**：`daily_metrics.csv` 为 Day 1—60 的当日增量。Day 0 没有此 CSV 行。要取 Day 21—60 增量，可以相加 CSV 的这些天，或用 JSON 的 Day 60 累计减去 Day 20 累计。

作品序列的 `videos_published` 在尚未发布时为 0，发布后为 1；作者序列则是截至当日已发布作品数。不要用“缺少一条 CSV 行”直接推导作品不存在，先查作品目录。

网站的「逛这个世界」另外计算新的推荐访问，不写入`community.snapshots`。访问Day d时，投稿前的候选目录截止d−1，投稿后截止d；推荐反馈均只累加`daily_metrics.csv`中`entity_type=video`且日期≤d−1的行，包括已完成关注/三连机会，不能只用最终点击量或简化后的JSON指标代替。`tools/build_recommendation_data.py`从这些公开输入生成浏览器数据；页面来源声明`browser_rule_visit`、截止日、种子、版本和卡序，并将历史会话ID置空。浏览记录不作为原实验的新曝光或点击。

## 每日 CSV 的粒度与数据字典

一行的唯一粒度是 **`run_id + simulation_day + entity_type + entity_id`**。同一次点开会同时记入作品、作者、分区、平台等统计视角；**不要把不同 `entity_type` 加在一起**，否则会重复计数。

| 字段 | 含义 |
| --- | --- |
| `metric_id` | 导出源中的行标识，不是跨运行的稳定业务主键。 |
| `run_id`, `simulation_day` | 模拟身份、当天编号。 |
| `entity_type`, `entity_id` | 统计视角及其对象 ID，见下表。 |
| `impressions` | 作品进入已成功会话首页候选卡的次数，不是独立观众人数。 |
| `clicks` | 模拟观众点开作品的次数。 |
| `completed_follow_opportunities` | 后续决策已完成、且具备关注机会的次数。 |
| `new_follows` | 新增关注次数。关注率分母应为已完成的关注机会数。 |
| `completed_post_click_opportunities` | 已完成后续点开决策的次数。 |
| `triples` | 三连次数；三连率分母应为已完成的后续点开决策数。 |
| `score` | 本次仅 `viewer_model` 行有值，表示该观众群体/模型当天的成功会话数，**不是模型得分**；其余为空。 |

| `entity_type` | `entity_id` 形式 | 本次行数 |
| --- | --- | ---: |
| `creator` | 创作者 ID | 6,000 |
| `video` | 作品 ID | 64,910 |
| `category` | 分区名 | 600 |
| `platform` | `platform` | 60 |
| `viewer_model` | JSON 数组字符串 `[cohort_role, model_alias]`，群体含 `population`、`reference` | 780 |
| `model_affinity` | JSON 数组字符串 `[creator_model_alias, viewer_model_alias]` | 2,940 |

平台、作者、作品等总账聚合了成功会话，**包括 Reference 参考观众**，不等同于只统计 Population 群体。仅靠 `creator` 行不能再拆出这两类观众。后续决策失败时，首页点开仍计为点开，但不自动计为已完成关注或三连机会。

计算点开率时使用 `sum(clicks) / sum(impressions)`，不要平均各作品或各日的点开率。曝光为 0 时，示例返回 JSON `null`，而不是宣称点开率为 0。`new_follows + triples` 是两种行为次数之和，不是独立人数。

## 可做什么，以及不能由此推导什么

- 可以阅读、检索作品与角色设定；研究同一世界内作品方向和模拟响应的变化；引用具体作品 ID 和模拟日，让观察可定位。
- 一组模型标签下的差异同时受人设、题材、供给、推荐与会话条件影响。这不是随机对照能力测评，不能直接写成模型能力排名或真人偏好结论。
- 观众评价和首页只是公开选集。完整数据库、逐次请求响应、所有会话、失败重试和成本记录不在此数据包中；无法据此完整复算费用、重放全部行为，或还原所有创作因果。
- `source_refs` 指向本次公开投影，而非私人运行目录。`world.json` **不是可接着运行的存档**。新实验应使用模拟框架另建 run ID；继续运行需要自己的运行数据库和对应配置。
- 数据可读不等于可任意再授权，复用范围请同时看 [RIGHTS.md](../RIGHTS.md)。

## 把示例用于自己的世界

示例接受 `--world PATH` 和（相应脚本的）`--metrics PATH`，不会覆盖 `data/`。JSON 必须具有上面同样的 `community.catalog` / `community.metrics` 分析结构；运行命令导出的其他报告或 `world-snapshot` 文件不保证具有此结构，**不要只改文件名就当成兼容数据**。CSV 则需相同字段、每日增量粒度与一致的 `run_id`。示例会对关键结构、日期范围和 CSV 重复粒度报错。

如果你只想分析新运行的 CSV，可在 `creator_day_metrics.py` 使用 `--no-world`；此时不补昵称，只按创作者 ID 输出。若一个文件包含多个 run ID，应明确传 `--run-id`，避免把几个世界混算。

拿到完整框架源码并完成自己的 `runs/demo` 后，可使用源码包内的工具导出同样可分析的结果：

```sh
python tools/export_run_data.py --run-dir runs/demo --output exports/demo
python examples/data/creator_works.py --world exports/demo/world.json --creator creator_001
python examples/data/creator_day_metrics.py --world exports/demo/world.json --metrics exports/demo/daily_metrics.csv --day 7
python examples/data/creator_change.py --world exports/demo/world.json --creator creator_001 --from-day 1 --to-day 7
```

这个导出器只读自己的运行数据库，输出已完成日的分析投影，不包含完整运行记忆或调用日志。其 `schema_version` 为 `ailiaili-analysis-projection-v1`，支持这些分析示例，但**不是成片网站的完整输入**，也不含正式世界的固定首页、精选评价或封面。独立数据下载包没有模拟框架和这个导出器；仅阅读本次结果无需它。

新的自有run导出还附`experiment_settings`：仅包含mode、seed、规模、发稿节奏与推荐参数等白名单字段，不包含模型路径、完整配置或自由填写的私人笔记。`compare_worlds.py`可利用它比较两次实验，并统一统计日期；已有正式世界投影没有这个摘要时会明确说明无法核对设置。
