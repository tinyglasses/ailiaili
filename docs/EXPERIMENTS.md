# 从一个好奇的问题开始

可以只读一个角色，可以让三个账号围绕同一件小事创作，也可以改一个推荐设置，观察小世界怎样变化。你不必先重跑100个账号和60天。

只想读故事，进入[UP主档案](../inspiration/README.md)。想研究原实验，使用[分析示例](../examples/data/README.md)。下面面向想亲自运行的人；安装方法见[快速开始](QUICKSTART.md)。

## 三个起点

| 模板 | 可以观察什么 | 默认规模 |
| --- | --- | --- |
| `stories` | 夜班便利店与洗衣房史官，怎样把日常小事展开成故事？ | 2作者、6观众、5天 |
| `workshop` | 面对同一件小事，解释者、动手派与观察者会怎样选切口？ | 3作者、6观众、5天 |
| `discovery` | 增加探索推荐后，曝光会不会分散到更多作者？作品本身发生了什么？ | 4作者、8观众、7天 |

这些是可修改的起点，不预设哪个模型、角色或推荐规则一定赢。默认全部使用stub，便于免费熟悉操作；真实创作差异需要配置自己的模型。例如，从同题创作开始：

```console
python -m ailiaili init my-workshop --template workshop
python -m ailiaili plan --config configs/local/my-workshop.yaml
python -m ailiaili prepare --config configs/local/my-workshop.yaml
python -m ailiaili run --config configs/local/my-workshop/world.yaml
python tools/serve.py --directory runs/my-workshop/site --port 8770
```

`init`创建小配置，`plan`解释设置，`prepare`展开配置，`run`才运行。已有文件不会被覆盖。修改账号见[自定义指南](CUSTOMIZE.md)。

## 让角色有不同的创作理由

“说话毒舌”和“想认真找出原因”是两回事。账号除了口吻，还需要创作方式、希望遇见的人、不愿越过的底线和长期矛盾。用`approach`选择起点：

| 值 | 更在意什么 | 创作方式 |
| --- | --- | --- |
| `storyteller` | 表达与作品、找到同类 | 人物与处境 |
| `explainer` | 弄懂问题、帮助理解 | 依据与机制 |
| `maker` | 能否使用、如何复核 | 步骤、条件与失败 |
| `observer` | 人的经验、细微关系 | 日常观察 |

四类展开成不同的创作动机、证据态度与矛盾。`personality`仍然用自己的话填写。还可以给一个作者加可选字段：

```yaml
approach: explainer
audience: 喜欢追问原因并愿意自己核对证据的读者。
boundary: 我不愿为了方便传播而隐藏反例和结论的适用条件。
tension: 我想解释清楚，又担心太多细节让真正好奇的人失去耐心。
```

这些文字进入LLM角色提示；stub不演绎它们。需要细调全部欲望、观众语法或提示协议时，使用[完整配置](EXTENDING.md)。

## 花钱之前，看清要跑什么

`plan`不调用模型、读取密钥值或创建数据库。它列出角色分工、初始和全程计划投稿数、计划浏览次数、四舍五入后的推荐卡位；LLM模式还显示模型分工、密钥变量名、各币种预算和逻辑调用范围。

重试会增加HTTP请求数。调用量也不等于费用，实际输入输出长度、服务返回和失败情况都会影响花费。默认预算可能不足以跑完整计划，工具不会自动追加预算。LLM占位符或价格未填完整时，预览会报缺项；本地检查不表示远端模型已经连通。

## 比较两次实验

把`discovery`复制成两个不同`run_id`的实验，保留相同seed、角色、观众和天数，只把第二份的`recommendation: balanced`改成`recommendation: exploratory`。名义比例分别是`表现/冷启动/探索=0.8/0.1/0.1`与`0.4/0.1/0.5`；实际首页受候选数量和作者上限影响。

两份运行完成后，假设目录是`runs/rec-base`与`runs/rec-explore`：

```console
python tools/export_run_data.py --run-dir runs/rec-base --output exports/rec-base
python tools/export_run_data.py --run-dir runs/rec-explore --output exports/rec-explore
python examples/data/compare_worlds.py --left exports/rec-base/world.json --right exports/rec-explore/world.json --output exports/recommendation-comparison.md
```

对照自动取双方都完成的同一天，也可加`--day 5`。它计算总点开÷总曝光、曝光最多作者的份额、零曝光作者数，并列出公开设置差异。不会平均各作者的点开率，也不把不同的最后一天直接相减。

除了指标，各挑一篇原稿读一读：某个账号是否只是得到更多曝光，还是创作方向也有变化？stub只能说明软件机制中的变化；单次真实模型运行也不足以证明因果或形成能力排名，可换几个seed重复观察。从同一段已发生的世界状态分叉，请用[世界快照与分支](QUICKSTART.md)。

## 把自己的成果交给别人读

```console
python tools/share_run.py --run-dir runs/my-workshop --output exports/my-workshop
```

得到`exports/my-workshop/`和同名ZIP。目录中有读者入口README、`reading/`中的逐账号设定与完整作品大纲、`data/`中的完成日作品与指标，以及文件校验清单`manifest.json`。

读者可直接读Markdown，或在你自己的GitHub里点开，不必运行模型。命令只生成本地包，不自动上传。包里不放数据库、运行记忆、逐次请求响应、密钥、私人笔记或开发历史；人设和作品原文仍需自己确认适合公开。它不是续跑快照，也不会自动变成成片版网站。

别人的一次参与可以很小：读一个账号、改一张角色卡、完成一个小实验，或分享一段有趣的作品序列。
