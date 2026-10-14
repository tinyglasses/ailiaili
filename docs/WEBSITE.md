# 网站：浏览、修改、构建

网站负责呈现结果，不运行模型。它与模拟器分开使用：改颜色、卡片、导航或封面，不会改变实验结果；运行新实验，也不需要先下载已有2002张封面。

想让所有人直接打开网址浏览，请按[GitHub Pages部署指南](PUBLISH.md)发布网页包。仓库已提供手动部署工作流，网站可由GitHub托管，无需自己保持服务器在线。

## 浏览UP主和作品

导航中的「所有 UP 主」覆盖全部100个账号，可以按名字/人设关键词查找、按分区/模型筛选，也可随机逛一个账号。目录是浏览工具，和某位观众实际收到的首页快照分开。

每个UP主有自己的空间：`?view=profile&creator=creator_038&day=60`。可展开人设摘要、按观察日筛选作品、切换新旧顺序，或从第一篇一路读到后一篇。账号、作品都可复制链接。

首页有两种方式，都可选Day 1—60，或用前一天/后一天逐日浏览：

- **逛这个世界**：默认入口。按该日可见的作品与原模拟推荐规则抽取40张卡片，点击「换一批」发起一次新访问；「想多看一点」为所选分区增加最多4个兴趣推荐机会，不把首页变成单一分区。下方分区按钮只筛选已抽到的卡片。
- **历史回放**：每天同一位参考观众实际收到的40张卡片，保留原会话与顺序。例如`?view=home&day=20&feed=history`。已有`snapshot=home_d020_viewer_ref_005_m05`等拍摄链接仍进入历史回放。

第40天「当天投稿前」只看第39天及以前的作品，「当天投稿后」可包含第40天新作；两者排序都只用截至第39天的累计反馈。动态模式下进入作者空间、连续阅读、作品统计也遵守这一时间边界。Day 0是初始作品状态，没有对应观众首页，可以通过不带`feed=dynamic`的作者空间链接查看。

这些是新的规则推荐访问，不冒充历史观众记录。推荐器保留原来的冷启动、探索、表现加权、新旧衰减、兴趣/关注预留位及每作者最多2张等规则；没有兴趣或关注的预留位按原规则补回全局池，因此最终桶比例并不总是60/20/20。浏览者当前视为新访客，无历史点击、关注或跨批曝光记忆；下一批可能再次出现之前的作品。浏览不修改原模拟统计，不运行新的AI创作或观众评价。

点击「固定这一页」把本次40张卡片、顺序与选取标签写入链接，并复制链接；随后可进入取景。普通推荐链接也保留种子，刷新不换批。固定链接还带数据修订号与规则版本；版本不符或卡片被截断时明确回退到历史回放，不悄悄给出另一份“固定页”。长期引用可同时保存网页包与镜头说明JSON。URL是可编辑的展示参数，不是历史事实的签名凭证。

推荐输入中标记为`utf8-lf-v1`的文本来源hash会先把换行统一为LF，再计算UTF-8内容的SHA256，避免Windows/Linux检出同一份数据时改变修订号。Release附件的`SHA256SUMS.txt`仍校验下载文件的原始字节。

浏览器的随机流是稳定的`lcg32-v1`；规则与Python推荐器对齐，但不声称相同seed能重演Python过去的一次会话。跨语言测试注入同一随机流比较结果。真正的原会话重现仍使用「历史回放」。

「改出我的 UP 主」打开本机配置编辑器，下载新的简版YAML；默认单作者、6观众、5天、stub模式。它提取部分设定并使用简版默认值，不复制原世界状态，也不在浏览器里调用模型。拿到源码后按[自定义指南](CUSTOMIZE.md)运行。

## 只想在本机浏览

最轻的方式是从Release下载网页包 `ailiaili-web.zip`，解压。正式链接见[下载页](../DOWNLOADS.md)。这是现有100位创作者世界的静态浏览版，图片已包含，不需要API Key。

有Python时，在解压后的网页目录运行：

```console
python -m http.server 8767 --bind 127.0.0.1
```

打开 `http://127.0.0.1:8767/`。如果8767被占用，改成8770。目录中应直接有index.html和assets，不要把整个研究工作目录当作网站根目录。

如果已经下载源码，可以用统一工具指定网页目录：

```console
python tools/serve.py --directory path/to/unpacked-web --port 8767
```

浏览网站不要求安装模拟器依赖。上面的本地服务仅监听127.0.0.1，不是公网部署命令。

## 查看自己刚跑出来的世界

按[快速开始](QUICKSTART.md)运行示例后：

```console
python -m ailiaili report --config configs/demo.yaml
python tools/serve.py --directory runs/demo/site --port 8770
```

这里打开的是模拟器随每个run生成的**研究报告页**。它包含该run的指标、作品和结果，不自动拥有事后制作的AI封面。

本仓库 `site/index.html` 则是为已发布的60天世界制作的社区浏览/拍摄页面。两者的数据格式不同；目前没有“把任何run直接换成成片页面”的通用适配器。不要把 `runs/demo/site/world_timeline.json` 重命名成 `data/world.json`：字段结构并不相同。

## 想改页面，改哪里

| 改动 | 入口 | 不需要改动 |
| --- | --- | --- |
| 颜色、字体、卡片布局 | `site/index.html` 的style区域 | 模拟器、数据库 |
| 原有拍摄视图、作者与作品主体 | `site/index.html`的JavaScript渲染函数 | 模型接口 |
| 目录、连续阅读和配置编辑器 | `site/explore.js`、`site/explore.css` | 模拟器、原始作品 |
| 查找、日期解析、角色配置导出 | `site/browse-core.js`，纯函数可单独测试 | 页面布局、模型服务 |
| 动态推荐规则与链接 | `site/recommendation-core.js`、`site/recommendation-ui.js` | 模型调用、原历史首页 |
| 浏览器逐日推荐输入 | `tools/build_recommendation_data.py` → `site/recommendation-data.js` | 私有数据库、运行日志 |
| 发布世界的文字/展示数据 | `data/world.json`，然后同步/重新构建 | 模拟运行代码 |
| 封面对应关系 | `community.cover_assets`；原PNG清单是 `data/covers.json` | 原文和模拟指标 |
| 自建run的默认报告 | `src/ailiaili/reports/templates/index.html` | 60天世界静态数据 |

单文件页面的JSON数据很长，不建议手工编辑内嵌数据块。编辑 `data/world.json` 后执行：

```console
python tools/sync_site_data.py
```

这会更新源码页面的数据块。保留原始公开数据，修改实验副本时在Git中标明是自己的改作，避免把自己补写的内容标成原实验产物。

修改公开作品目录、`data/daily_metrics.csv`或`configs/reference/world.yaml`后，再执行`python tools/build_recommendation_data.py`。工具从公开数据生成逐日六项计数与推荐参数，不读原数据库；生成文件约1.4 MB。修改抽样算法时同步更新`recommendation-core.js`里的规则版本及跨语言测试。这里只支持已发布世界的数据契约，不自动适配任意新run。

## 构建没有图片的轻量页面

在仓库根目录、已建立的虚拟环境中：

```console
python -m pip install -e ".[web]"
python tools/build_web.py --text-only --output dist/text-site
python tools/serve.py --directory dist/text-site --port 8770
```

这个例子不需要下载4.53 GB原图，使用现有标题和色块封面。输出目录必须是新目录，工具拒绝覆盖已有产物。

## 构建带图片的网页版本

先按[下载说明](../DOWNLOADS.md)导入原始封面，再运行：

```console
python -m pip install -e ".[web]"
python tools/build_web.py --output dist/site --workers 4
python tools/serve.py --directory dist/site --port 8770
```

工具从 `data/world.json` 读取数据、从 `site/index.html` 读取页面模板，为每张封面产生640/q80和1280/q88两档WebP，配置srcset与懒加载，保留原PNG不变。所有处理在本机进行，不调用图片生成服务。

已有发布世界的完整网站约481 MB；这是一次实测，不保证替换后的任意数据仍是同样大小。`build-summary.json` 记录本次大小，构建器按保守的10亿字节检查部署预算。只把输出目录作为站点上传，不上传仓库父目录或运行日志。

网页衍生图保留AI来源说明及原文件hash。原PNG内的C2PA签名不会直接复制成新文件的有效签名；原图包保留其原始凭据。公开发布时仍应清楚说明AI生成、虚拟账号和模拟指标。

## 拍摄接口

页面工具里的「进入取景」继续使用同一套成片布局。URL保存观察日、作品、分页和镜头进度；`layout=cover`为封面镜头，`layout=quote`为原稿摘句镜头，`step/from/to/split/highlight`控制原文项目与节选。UTF-16位置用于浏览器字符串定位，不能把字数直接当成索引。

自动拍摄可以调用`window.AiliAiliStage.setState({...})`、`seek(0..1)`、`getSources()`和`getLayers()`。完成切换后应等待`window.__AILIAILI_READY__ === true`或`#surface[data-capture-ready="true"]`再截图；取景模式会等待图片和字体，避免卡片已经出现但图片尚未解码的空白帧。缺图会回退为文字封面，就绪不等于有原图。

两期对照和观众点评仍包含特定账号的编辑样本；目录、账号作品墙、普通作品阅读及封面/摘句镜头可用于其他已有UP主。不要把精选对照和点评当成100账号都拥有的自动分析。

修改独立浏览逻辑后，有Node.js时运行`node --test tests/test_browse_core.cjs tests/test_recommendation_browser.cjs`；完整框架测试为`python -m pytest`，其中跨语言规则测试需要Node.js，缺少时会明确跳过。浏览器还需验证桌面/手机、真实快照的作品ID及卡序、动态换批与固定页、取景和返回，不能只看卡片数量。

## 接入自己世界的成片风格页面

这是适合贡献的扩展点，当前需要适配代码：

1. 读取自己run导出的 `world_timeline.json`、`summary.json`，或通过内部数据库模型读取已完成日事实。
2. 生成页面需要的 `community.catalog.creators/works/creator_work_ids`、累计指标、真实首页session与卡序。
3. 移除当前页面对特定示例创作者及Day60范围的假设，再处理空世界、单作品、不同人口规模；首页日期从新世界实际导出的快照读取。
4. 没有封面时用文字卡；如果另生成图片，说明它们是展示素材。
5. 验证URL中的日期、session、作品ID与实际内容一致。仅卡片数量相同不足以证明适配正确。

接口字段与统计口径见[数据说明](DATA.md)，贡献入口见[CONTRIBUTING](../CONTRIBUTING.md)。
