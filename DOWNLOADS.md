# 按用途下载

[直接打开 AiliAili](https://tinyglasses.github.io/ailiaili/) · [v0.1.1 全部下载](https://github.com/tinyglasses/ailiaili/releases/tag/v0.1.1) · [源码仓库](https://github.com/tinyglasses/ailiaili)

只想浏览时，打开在线网站即可，无需安装或运行本地服务。想发布自己的版本，可按[GitHub Pages部署指南](docs/PUBLISH.md)操作。

| 目的 | 获取什么 | 不必下载 |
| --- | --- | --- |
| 只逛这次实验的世界 | [在线站](https://tinyglasses.github.io/ailiaili/)，或 [ailiaili-web.zip](https://github.com/tinyglasses/ailiaili/releases/download/v0.1.1/ailiaili-web.zip) | Python依赖、原PNG、模拟数据库 |
| 在自己电脑运行或修改模拟器 | 仓库ZIP或 [ailiaili-source.zip](https://github.com/tinyglasses/ailiaili/releases/download/v0.1.1/ailiaili-source.zip) | 所有封面包 |
| 研究已经发生的作品与指标 | [ailiaili-data.zip](https://github.com/tinyglasses/ailiaili/releases/download/v0.1.1/ailiaili-data.zip)，含数据、数据字典和标准库示例 | 图片、模型服务、实验状态数据库 |
| 只阅读UP主、找写作灵感 | GitHub里的 [inspiration/](inspiration/README.md)，或 [ailiaili-inspiration.zip](https://github.com/tinyglasses/ailiaili/releases/download/v0.1.1/ailiaili-inspiration.zip) | 网站、Python、图片 |
| 使用原图拍摄或重建网页图片 | `ailiaili-covers-*.zip`，共约4.53 GB | 只看网站时无需这些包 |

所有附件对应同一发布世界。[SHA256SUMS.txt](https://github.com/tinyglasses/ailiaili/releases/download/v0.1.1/SHA256SUMS.txt)提供下载校验值；五个可选原图分包可在[Release 附件列表](https://github.com/tinyglasses/ailiaili/releases/tag/v0.1.1)获取。

所有 ZIP 均附带 `RIGHTS.md`、MIT 与 CC BY 4.0 许可文件。代码与内容分别适用其许可，均允许商用；具体署名方式见[授权说明](RIGHTS.md)。旧 v0.1.0 附件中属于本项目的材料也获得同样授权，已有图片无需重新下载。

## 网页包

`ailiaili-web.zip`解压后直接包含index.html、页面脚本与assets/，完整约481 MB。使用[网站指南](docs/WEBSITE.md)中的本地HTTP服务浏览；无需安装模拟器或设置模型Key。

## 源码与运行

下载ZIP即可本地使用；Fork只在你想保存自己的GitHub版本或贡献时需要。按[快速开始](docs/QUICKSTART.md)建立Python环境并运行stub示例。

## 数据包

包括data/、数据字典与examples/data/，不包括原始运行数据库。它能回答公开字段范围内的分析问题，不能作为从Day60续跑的状态快照。

## 灵感包

包括100个账号的设定与作品清单、阅读示例、角色卡模板。没有安装要求。二创和再分发的授权范围以[许可说明](RIGHTS.md)为准。

## 原图包

将全部 `ailiaili-covers-*.zip` 下载到同一文件夹，在源码目录运行：

```console
python tools/install_covers.py path/to/downloads
python tools/serve.py
```

工具按路径、作品ID与SHA256导入到site/assets。完整映射在data/covers.json。普通clone不包含PNG；无图阅读可按网站指南构建文字版，不必为了跑模拟而下载原图。
