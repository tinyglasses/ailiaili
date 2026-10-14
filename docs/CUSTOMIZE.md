# 改一个文件，创建自己的世界

从 [configs/custom-world.yaml](../configs/custom-world.yaml) 开始。模型、账号性格、创作方向、分区和观众都写在同一份YAML里，不需要先看底层人设格式。环境安装见[快速开始](QUICKSTART.md)。

也可以先在网站打开一个UP主，点击「改出我的 UP 主」，修改账号名、性格、方向和分区，下载自己的YAML。将文件放到源码目录，使用下面同样的`plan / prepare / run`流程；下载的是新实验的简版起点，不是原账号的续跑状态。

想先选一个玩法，可以运行`python -m ailiaili init my-world --template workshop`，生成自己的配置。可选`stories / workshop / discovery`，说明见[实验入口](EXPERIMENTS.md)。`python -m ailiaili plan --config configs/local/my-world.yaml`会先解释角色分工、计划投稿、推荐卡位与调用量；不会启动实验。

## 先跑通：两条命令

在项目根目录、已安装项目的Python环境中执行：

```console
python -m ailiaili prepare --config configs/custom-world.yaml --output configs/local/my-world
python -m ailiaili run --config configs/local/my-world/world.yaml
```

原样运行是2个作者、6个观众、5天的 **stub小世界**，不需要密钥、不调用模型。然后打开自己的报告：

```console
python tools/serve.py --directory runs/my-world/site --port 8770
```

`prepare`只做本地校验和展开；真正推进模拟的是`run`。想只检查配置、不写文件：

```console
python -m ailiaili prepare --config configs/custom-world.yaml --check
```

检查不发起网络请求，也不验证远端模型是否实际可用。

## 最常改的字段

| 想改什么 | 填哪里 | 示例 |
| --- | --- | --- |
| 世界名字、时长、随机种子 | `run_id / days / seed` | `my-ghost-world / 7 / 42` |
| 分区 | `categories` | `[怪谈, 日常史诗, 桌面种菜]`，名称自定，不限原来的分区 |
| UP主 | `creators`中的每一项 | 加一项就增加一个作者，人数和ID自动生成 |
| 性格与说话方式 | `personality` | 一本正经，胆子很小，遇到怪事先看劳动合同 |
| 创作方式与内在矛盾 | `approach`，可选`audience / boundary / tension` | 讲故事、解释原理、设计与尝试、观察生活四种起点 |
| 想做的内容 | `goal / topics` | 账号长期方向，以及1—2个最初想尝试的题材 |
| 观众规模与口味 | `viewers.count / interests / personality` | 6人，偏爱怪谈，喜欢具体故事、讨厌说教 |
| 模型 | 默认`model`、每作者的`model`、`viewers.model` | 作者和观众可以不同，作者之间也可以不同 |
| 发稿节奏 | `publish_every` | 每位作者每隔2个模拟日投稿，作者之间错开 |
| 推荐倾向 | `recommendation` | `balanced`或`exploratory`，可做规则对照 |

例如新增一个账号，只需在`creators`里增加：

```yaml
  - name: 阳台植物议会
    category: 桌面种菜
    personality: 温和固执，习惯把植物当成有意见的室友
    goal: 用合租日记的方式讲种菜遇到的小问题
    topics: [薄荷拒绝与葱合租, 番茄争夺窗边阳光]
```

同时把`桌面种菜`加入`categories`；也可以加进观众的`interests`。分区名称必须对应，拼错会报错。观众兴趣不填时使用全部分区；填写时表示优先兴趣，仍可能对其他已配置分区保持中等兴趣。

**stub使用固定规则和文案模板。** 名字、分区、题材、观众兴趣会进入运行数据；性格被保存，但没有模型理解和演绎它。要观察不同性格怎样创作、不同观众怎样判断，需要切到LLM模式。界面不会把stub模板当成真实模型行为。

## 换成自己的模型

将`mode`改为`llm`，填写`models`里的模型ID与价格，再使用新的`run_id`和输出目录。别名只是本项目内的名字，例如`main`；`model_id`才是服务端识别的模型名称。

默认通过OpenRouter，也可选`volcengine_ark`，或使用通用的`openai_compatible`。这里的“兼容”指 **Chat Completions** 请求和响应格式，不包括Responses API、Anthropic原生接口或任意聊天网页。

自定义服务地址示例：

```yaml
mode: llm
model: my_service
models:
  my_service:
    provider: openai_compatible
    endpoint: https://your-model-service.example/v1/chat/completions
    model_id: YOUR_MODEL_ID
    api_key_env: MY_MODEL_KEY
    supports_json_mode: true
    input_per_million: null
    output_per_million: null
```

这是需要填写的片段：服务地址、模型ID、价格都要换成自己的实际值。`endpoint`填写完整请求地址，包含`/chat/completions`，工具不会猜测并追加路径。模型要能遵循提示返回严格JSON；不支持`response_format`时设`supports_json_mode: false`，引擎仍会校验返回内容。

供应商若要求`max_completion_tokens`，添加`token_limit_parameter: max_completion_tokens`；若不接受温度参数，添加`supports_temperature: false`。通用适配器不发送供应商专有的`thinking`、`reasoning`、`provider`和`seed`字段。

无鉴权的本地服务可显式设置`api_key_env: null`，`endpoint`改为自己的本机完整地址，并把两项价格设为`0`。服务必须已经启动，项目不会替你下载模型或配置GPU。兼容服务的接口形式可参照[vLLM官方说明](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)，不同模型的能力仍需自己确认。

密钥只放当前终端的环境变量，不写进YAML：

```powershell
$env:MY_MODEL_KEY = "在本机填写自己的密钥"
```

```bash
export MY_MODEL_KEY='在本机填写自己的密钥'
```

代码不会自动读取`.env`；`prepare --check`不需要密钥。实际`run`在初始化前检查所需变量是否存在。默认费用以USD填写，使用CNY服务时设`currency: CNY`；`budget`是本地预算估计，不是供应商账单保证，也不使用ChatGPT订阅额度。

多个模型时，在`models`中定义多个别名，再按角色指定：

```yaml
model: main
creators:
  - name: 夜班便利店
    category: 怪谈
    personality: 一本正经，遇到怪事先看劳动合同
    goal: 把便利店值夜班写成生活怪谈
    topics: [冰柜为什么不肯下班]
    model: writer
viewers:
  count: 6
  interests: [怪谈]
  model: reader
```

这里`main / writer / reader`必须在同一文件的`models`中定义。未单独指定的角色使用默认模型，重复使用同一别名不需要重复填服务配置。默认单路、请求启动间隔1秒；若需要更多运行参数，使用[完整配置指南](EXTENDING.md)。

## 简版配置会生成什么

`configs/local/my-world/`包含：

- `world.yaml`：引擎可以直接运行的配置。
- `creators.yaml`：展开的完整人设；性格进入“你的声音”，目标进入创作直觉，题材进入初始想法。
- `viewers.yaml`：围绕自定义分区建立的观众兴趣与偏好。
- `models.yaml`：仅LLM模式生成，包含服务、价格、预算和密钥变量名，不含密钥值。

简版按`approach`补入不同的创作欲望、证据态度与矛盾；未填写时采用`storyteller`。可用`audience / boundary / tension`覆盖想遇见的观众、底线与长期矛盾。观众语法、对平台反馈的关系等仍有通用默认值。它们是方便起步的预设；完整含义与使用示例见[实验入口](EXPERIMENTS.md)。

字段拼错、重复账号名、不存在的分区、缺失的模型别名或价格，都会在写文件之前报错。输出目录必须是新的。LLM配置展开后会记录依赖hash，避免续跑时悄悄换人设或模型。

继续原世界使用原`world.yaml`运行；改角色、模型或分区时，修改简版源文件，换新的`run_id`，再准备到一个新目录。若整体搬动准备好的目录，绝对依赖路径需要重新准备；原数据目录与已开始的实验应保留。分享自己的实验时可分享这份小配置，模型密钥由运行者自己提供。
