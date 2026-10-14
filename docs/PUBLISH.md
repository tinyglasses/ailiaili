# 把网页放到 GitHub Pages

GitHub Pages托管本项目的静态网页。发布后，观众打开公开网址即可浏览，项目所有者不需要保持电脑开机、运行本地服务或购买自己的服务器。线上仍是这套社区浏览和拍摄页面，动态推荐在浏览器计算，不调用模型。

本项目的浏览入口为 [AiliAili](https://tinyglasses.github.io/ailiaili/)。本仓库已经提供部署工作流，但上传代码不会自动上线；下面说明如何在自己的仓库首次发布及更新。

## 首次发布

1. 在GitHub创建仓库，将整理后的源码放在仓库根目录，包含`.github/workflows/`。不要把网页ZIP、原PNG包、自己的`runs/`或研究工作目录一起提交到Git历史。
2. 创建Release，例如标签`v0.1.0`，上传同批次的`ailiaili-web.zip`和`SHA256SUMS.txt`。数据、灵感和原图包也可作为Release附件，部署网页只需要前两项。ZIP应直接包含`index.html`与`assets/`，不能再套一层文件夹。
3. 在仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。使用GitHub Free时，这个方案需要公开仓库；其他方案的可用性以账号计划为准。
4. 打开 **Actions → Publish website → Run workflow**，选择默认分支，填写第2步的Release标签并运行。
5. 等待`prepare`和`deploy`成功，点击部署结果中的网站地址。项目网站地址通常形如`https://你的用户名.github.io/仓库名/`；以GitHub实际返回的地址为准。
6. 把实际网站链接填到仓库About的Website，以及README和DOWNLOADS的浏览入口。观众只需这一个链接，不需要下载、Fork或安装Python。

工作流从当前仓库的指定Release下载网页包，核对SHA256、解压后大小和静态文件范围，再交给GitHub托管。使用GitHub自动提供的工作流令牌，不需要填写个人Token或模型Key。工作流不会发布仓库根目录，也不会运行模拟器或生成图片。

在GitHub上查看`site/index.html`的源码，并不等于打开网站。请分享部署成功后的Pages网址。个人设备访问GitHub服务的网络情况仍会影响打开速度。

## 更新与回退

新版本重新构建网页包、计算对应SHA256并放入新的Release，再手动运行`Publish website`选择新标签。只修改源码或替换源PNG，不会自动更新线上网站。

需要回退时，重新选择保留着完整网页包与校验文件的旧Release标签部署。这个流程按已打包版本发布，避免在每次文档修改时重新上传整站图片。

自己修改网页后，先按[网站构建指南](WEBSITE.md)生成`dist/site`（需要原图时先导入；文字版可用`--text-only`）。然后只把公开网页文件压入新ZIP，构建日志不入包：

```console
cd dist/site
python -m zipfile -c ../ailiaili-web.zip index.html .nojekyll browse-core.js explore.js explore.css recommendation-core.js recommendation-data.js recommendation-ui.js cover-derivatives.json assets
cd ..
```

现在位于`dist`目录，内有新的`ailiaili-web.zip`。为旧版保留独立的下载目录或Release附件；这条打包命令会覆盖同路径的旧ZIP。

`SHA256SUMS.txt`采用标准格式，每行是`64位SHA256值  文件名`。仅网页包的最小校验文件也可以这样生成（在ZIP所在目录执行）：

```console
python -c "from pathlib import Path; import hashlib; p=Path('ailiaili-web.zip'); h=hashlib.file_digest(p.open('rb'),'sha256').hexdigest(); Path('SHA256SUMS.txt').write_text(h+'  '+p.name+'\n', encoding='utf-8')"
```

已有同批完整`SHA256SUMS.txt`时直接使用它，无需覆盖其余附件的校验值。校验保证下载与清单一致，不是对发布者身份的数字签名。

## 本地验证部署输入

以下命令与工作流使用同一个准备步骤，只读ZIP和校验文件，将允许公开的静态文件写入新目录：

```console
python tools/prepare_pages.py --archive path/to/ailiaili-web.zip --checksums path/to/SHA256SUMS.txt --output dist/pages
python tools/serve.py --directory dist/pages --port 8770
```

只需要Python标准库，不需要安装模型或图片依赖。工具拒绝覆盖现有目录，拒绝源码、运行日志、链接文件和超出站点大小预算的包。自建版本增加了新的网页资源类型时，同时更新`tools/prepare_pages.py`的公开文件范围。

## 大小与费用边界

当前完整网页解压后约481 MB，部署工具按10亿字节做保守上限校验；4.53 GB原PNG不进入Pages。GitHub Pages目前的发布站点上限为1 GB，月流量软限额100 GB。月流量是所有访客实际下载资源的累计量，不是访客人数，也不是每次必定下载整站481 MB；本页会按需加载封面。超出软限额可能需要调整托管方式。[GitHub Pages官方限制](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)

这套部署不依赖自购服务器。代码测试工作流会在提交/PR时运行无模型测试；Pages发布工作流仅在手动触发时运行。Actions用量与仓库计划有关，详见[GitHub Pages自定义工作流说明](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。

公开浏览和允许别人改编、再分发是不同安排；项目的代码、数据与图片授权状态见[RIGHTS.md](../RIGHTS.md)。
