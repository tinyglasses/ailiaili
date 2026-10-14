"""Export readable creator archives from the public world projection (stdlib only).

No model calls, image downloads, or edits to the world data are performed.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "inspiration"
FORMAL_RUN_ID = "ailiaili-formal-v1.2-100c-60d"
EXAMPLES = {
    "creator_038": {
        "name": "NPC也有尊严",
        "intro": "从副本小怪的三班倒开始读，再看看这些边缘角色后来怎样串到一起。",
        "ids": ["video_creator_038_d000_01", "video_creator_038_d006_01", "video_creator_038_d060_01"],
        "question": "给你自己的游戏世界选一个只负责重复动作的小角色：如果它今天轮休，玩家会遇到哪一件具体的小麻烦？先写三个日常细节，不急着给它安排悲惨身世。",
    },
    "creator_086": {
        "name": "两天小企划",
        "intro": "一条周末规则，加上一个随时可以放弃的人。这里的挑战和执行结果都是模拟。",
        "ids": ["video_creator_086_d000_01", "video_creator_086_d006_01", "video_creator_086_d060_01"],
        "question": "为你自己的虚构周末角色加一条安全、便宜、随时能退出的小规则：第一天哪件小事让它觉得有趣，第二天又会因为什么主动放弃？别把退出写成受罚。",
    },
    "creator_050": {
        "name": "把日常拍成史诗",
        "intro": "倒垃圾和取外卖都变成了王国远征。读的时候留意：除了大词，原稿还用了哪些小事来拆台？",
        "ids": ["video_creator_050_d000_01", "video_creator_050_d006_01", "video_creator_050_d048_01"],
        "question": "选一件你熟悉的小事，给自己的角色写一段庄严旁白，再让一个很具体的生活细节打断它。去掉“命运”“王国”“远征”这些词以后，这个场面还好笑吗？",
    },
}
PERSONA_ORDER = [
    "创作缘起", "最重要的创作欲望", "同样真实的第二欲望", "你想找到的观众",
    "你的创作直觉", "你的声音", "你如何对待证据", "你如何看标题与封面",
    "你与平台反馈的关系", "你不愿轻易越过的身份底线", "你长期带着的矛盾",
    "可自然跨入的分区", "创世时的直觉",
]
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_catalogue(catalogue: dict) -> None:
    """Keep imported IDs inside output filenames/anchors and check ownership."""
    creators, works = catalogue["creators"], catalogue["works"]
    for key, creator in creators.items():
        identifier = creator.get("creator_id")
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key):
            raise ValueError(f"Unsafe creator key: {key!r}")
        if identifier != key:
            raise ValueError(f"Creator key and creator_id differ: {key!r}")
    for key, work in works.items():
        identifier = work.get("video_id")
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key):
            raise ValueError(f"Unsafe work key: {key!r}")
        if identifier != key:
            raise ValueError(f"Work key and video_id differ: {key!r}")
        if work.get("creator_id") not in creators:
            raise ValueError(f"Work creator does not exist: {key!r}")


def literal(value: object) -> str:
    """Preserve source words while preventing source text from becoming Markdown."""
    text = html.escape(str(value), quote=False)
    for char in "\\`*_{}[]|":
        text = text.replace(char, "\\" + char)
    return text


def link(label: str, destination: Path, document: Path, anchor: str = "") -> str:
    relative = Path(os.path.relpath(destination, document.parent)).as_posix()
    return f"[{literal(label)}]({quote(relative, safe='/.-_')}" + (f"#{anchor}" if anchor else "") + ")"


def rights_path(output: Path) -> Path:
    return ROOT / "RIGHTS.md" if output == DEFAULT_OUTPUT else output / "RIGHTS.md"


def source_note(document: Path, source_label: str, output: Path) -> list[str]:
    return [
        "这是 AI 内容社区的模拟档案，人物不是现实 UP 主。作品为模拟中生成的标题、简介与内容大纲，不能当作真实执行记录或已经完成的视频。",
        "",
        "角色设定、账号声明及作品文字均来自输入数据 `" + literal(source_label)
        + "`；模型名称仅说明当次记录来源。正式世界的数据口径见完整项目中的 `docs/DATA.md`。"
        + link("授权与署名", rights_path(output), document) + "。",
        "",
    ]


def field_lines(key: str, value: object) -> list[str]:
    if isinstance(value, list):
        return [f"**{literal(key)}（原文）**", "", *[f"- {literal(v)}" for v in value], ""]
    return [f"**{literal(key)}（原文）**：{literal(value)}", ""]


def work_lines(work: dict, outline: bool) -> list[str]:
    lines = [f'<a id="{work["video_id"]}"></a>', "",
             f'### Day {work["published_day"]:02d} · {literal(work["title"])}', "",
             f'`{work["video_id"]}`', "", f'**原始简介**：{literal(work.get("premise", ""))}', ""]
    if outline:
        lines.extend(["**内容大纲（原文）**", ""])
        lines.extend(f"{i}. {literal(text)}" for i, text in enumerate(work.get("outline", []), 1))
        lines.append("")
    return lines


def write_creator(output: Path, creator: dict, works: list[dict], include_outlines: bool,
                  enabled_examples: set[str], source_label: str) -> None:
    creator_id = creator["creator_id"]
    document = output / "creators" / f"{creator_id}.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# {literal(creator["name"])}', "",
             link("返回全部 UP 主", output / "README.md", document)
             + " · " + link("写自己的角色", output / "TEMPLATE.md", document), "",
             f'`{creator_id}` · {literal(creator["category"])} · {len(works)} 份作品 · 模型记录名：`{creator["model_alias"]}`', ""]
    lines += source_note(document, source_label, output)
    if creator_id in enabled_examples:
        lines += [link("先读三篇完整大纲与改编练习", output / "examples" / f"{creator_id}.md", document), ""]
    declaration = creator.get("declaration") or {}
    if declaration.get("我想做的账号"):
        lines += ["## 它想做什么", "", "> " + literal(declaration["我想做的账号"]), "", "以上为账号初始声明原文。", ""]
    lines += ["## 角色设定原文", ""]
    persona = creator.get("persona") or {}
    keys = [k for k in PERSONA_ORDER if k in persona]
    keys += [k for k in persona if k not in keys and k not in {"账号名", "主分区"}]
    for key in keys:
        lines += field_lines(key, persona[key])
    if declaration:
        lines += ["<details>", "<summary>展开完整初始声明</summary>", ""]
        for key, value in declaration.items():
            lines += field_lines(key, value)
        lines += ["</details>", ""]
    lines += ["## 按日期读作品", "", "按模拟 Day 从早到晚排列。简介保留原始 `premise` 全文，不另造一句推荐语。Day 0 是初始作品，不是现实发布日期。", ""]
    for work in works:
        lines += work_lines(work, include_outlines)
    if not include_outlines:
        lines += ["## 想读这个账号的全部大纲", "", "这份网页档案默认只列标题与原始简介。下载代码后可在项目根目录按账号导出完整大纲，无需安装依赖或调用模型：", "", "```console",
                  f"python tools/export_inspiration.py --creator {creator_id} --include-outlines --output exports/inspiration-full",
                  "```", "", "这是读取已经公开的数据，不会生成新内容。也可在公开数据里按上面的作品 ID 查找 `outline`。", ""]
    lines += [link("返回全部 UP 主", output / "README.md", document), ""]
    document.write_text("\n".join(lines), encoding="utf-8")


def write_example(output: Path, creator: dict, catalogue: dict, source_label: str) -> None:
    creator_id = creator["creator_id"]
    spec = EXAMPLES[creator_id]
    if creator["name"] != spec["name"]:
        raise ValueError(f"Example identity mismatch: {creator_id}")
    document = output / "examples" / f"{creator_id}.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'# {literal(creator["name"])}：三篇原稿', "",
             link("完整账号档案", output / "creators" / f"{creator_id}.md", document)
             + " · " + link("全部 UP 主", output / "README.md", document), "", spec["intro"], ""]
    lines += source_note(document, source_label, output)
    lines += ["下列标题、简介与编号大纲为原文；本页的导读和最后的练习由发布整理时补充，未作为模拟中的作品或角色发言。", ""]
    for video_id in spec["ids"]:
        work = catalogue[video_id]
        if work["creator_id"] != creator_id:
            raise ValueError(f"Example ownership mismatch: {video_id}")
        lines += work_lines(work, True)
    lines += ["## 给读者的改编练习", "", "**以下问题由发布整理时编写，不是实验生成原文，也不是这个账号已经发表的作品。**", "", spec["question"], "",
              "可以先用自己的角色和生活材料写四条大纲；愿意继续的话，再给它写第二期，看看哪些习惯值得保留。许可状态以 "
              + link("RIGHTS.md", rights_path(output), document) + " 为准。", "",
              link("打开空白创作卡", output / "TEMPLATE.md", document), ""]
    document.write_text("\n".join(lines), encoding="utf-8")


def write_index(output: Path, creators: list[dict], by_creator: dict, total: int, include_outlines: bool,
                enabled_examples: set[str], source_label: str, run_id: str) -> None:
    document = output / "README.md"
    lines = ["# 逐个认识这些 AI UP 主", "", "不需要 fork、下载图片或启动网页。在 GitHub 里点一个名字，就能读它的角色设定和逐日作品，看看一个点子如何被反复使用，又在哪里开始变化。", "",
             f"本索引收录 **{len(creators)} 个账号、{total} 份作品**。每个账号单独一页，"
             + ("本次按需导出已附每篇的完整原始大纲。" if include_outlines else
                "默认只保留标题和原始简介；详细示例另附真实大纲。" if enabled_examples else
                "只保留标题和原始简介，完整大纲可按需导出。"), ""]
    lines += source_note(document, source_label, output)
    if run_id:
        lines += [f"本次输入世界：`{literal(run_id)}`。", ""]
    examples = [c for c in creators if c["creator_id"] in enabled_examples]
    if examples:
        lines += ["## 先读这几个", ""]
        for creator in sorted(examples, key=lambda c: list(EXAMPLES).index(c["creator_id"])):
            spec = EXAMPLES[creator["creator_id"]]
            lines += ["- " + link(creator["name"], output / "examples" / f'{creator["creator_id"]}.md', document) + "：" + spec["intro"]]
        lines += ["", "这些示例把原稿与读者练习分开标明；不要求先懂整个模拟项目。", ""]
    lines += ["## 怎么读，怎么借一个点子", "",
              "先读账号的“它想做什么”和“你的声音”，再顺着日期挑几期。可以只读有趣的一篇，也可以比一比早期和后期：题目变了，讲话方式、取材习惯有没有跟着变？这些变化留给你判断，不预设它一定成长或退步。", "",
              "想写自己的东西，可以只改一个条件：换角色的生活环境，换它关心的人，或者保留语气、换一件小事。"
              + link("这张空白创作卡", output / "TEMPLATE.md", document) + "给了一个不用代码的起点，也说明了如何接到模拟器配置。", "",
              "## 按分区找", ""]
    categories = sorted({c["category"] for c in creators})
    anchors = {category: f"category-{i:02d}" for i, category in enumerate(categories, 1)}
    lines += [" · ".join(f'[{literal(cat)}](#{anchors[cat]})' for cat in categories), ""]
    for category in categories:
        lines += [f'<a id="{anchors[category]}"></a>', "", f"### {literal(category)}", "", "| 账号 | 作品数 |", "| --- | ---: |"]
        for creator in sorted((c for c in creators if c["category"] == category), key=lambda c: c["name"]):
            creator_id = creator["creator_id"]
            lines += ["| " + link(creator["name"], output / "creators" / f"{creator_id}.md", document) + f" | {len(by_creator[creator_id])} |"]
        lines.append("")
    lines += ["## 按名字找", "", "可用浏览器“在页面中查找”搜索账号名；以下按名称的 Unicode 顺序排列，不是排名。", ""]
    lines += [" · ".join(link(c["name"], output / "creators" / f'{c["creator_id"]}.md', document) for c in sorted(creators, key=lambda c: c["name"])), "",
              "## 数据与导出", "", "这些档案由完整项目中的 `tools/export_inspiration.py` 从输入数据生成，不调用模型。角色设定与初始声明保持原文，作品列表按 `published_day` 排序。"
              + ("本次导出按要求包含完整大纲。" if include_outlines else "默认不重复每篇完整大纲，可按账号另行导出；若本次包含正式世界详细示例，示例页会展示选中作品的大纲。"), "",
              "在项目根目录重新生成全部简版档案：", "", "```console", "python tools/export_inspiration.py", "```", "",
              "只导出指定账号，并附上它的每篇完整大纲：", "", "```console", "python tools/export_inspiration.py --creator creator_038 --include-outlines --output exports/npc-reading", "```", "",
              "这些命令需要完整项目中的脚本和数据；独立灵感包只负责阅读，无需执行。可重复传入 `--creator` 选择多个账号。导出不需要 API Key；它不会调用模型，也不会改动正式世界。", "",
              link("项目／灵感包首页", ROOT / "README.md" if output == DEFAULT_OUTPUT else output / "README.md", document)
              + " · " + link("授权与署名", rights_path(output), document), ""]
    document.write_text("\n".join(lines), encoding="utf-8")


def export_inspiration(world: Path, output: Path, *, creator_ids: list[str] | None = None, include_outlines: bool = False) -> dict:
    creator_ids = creator_ids or []
    data = json.loads(world.read_text(encoding="utf-8"))
    catalogue = data["community"]["catalog"]
    validate_catalogue(catalogue)
    unknown = sorted(set(creator_ids) - set(catalogue["creators"]))
    if unknown:
        raise ValueError("Unknown creator IDs: " + ", ".join(unknown))
    chosen = set(creator_ids) if creator_ids else set(catalogue["creators"])
    output = output.resolve()
    if (creator_ids or include_outlines or world.resolve() != (ROOT / "data/world.json").resolve()) and output == DEFAULT_OUTPUT:
        raise ValueError("Use --output for selected creators, full outlines, or another world; preserve the default archive")
    if output != DEFAULT_OUTPUT and output.exists():
        raise ValueError("Output already exists; choose a new directory to avoid overwriting or mixing archives")
    creators = [c for key, c in catalogue["creators"].items() if key in chosen]
    enabled_examples = {
        cid for cid, example in EXAMPLES.items()
        if data.get("run_id") == FORMAL_RUN_ID
        and cid in chosen
        and catalogue["creators"][cid]["name"] == example["name"]
        and all(vid in catalogue["works"] and catalogue["works"][vid]["creator_id"] == cid for vid in example["ids"])
    }
    by_creator = {
        c["creator_id"]: sorted((w for w in catalogue["works"].values() if w["creator_id"] == c["creator_id"]),
                                key=lambda w: (w["published_day"], w["video_id"]))
        for c in creators
    }
    source = world.resolve()
    source_label = source.relative_to(ROOT).as_posix() if source.is_relative_to(ROOT) else source.name
    output.mkdir(parents=True, exist_ok=True)
    if output != DEFAULT_OUTPUT:
        # A selected or reader-created archive can be moved on its own: no links
        # back to the author's checkout are required merely to read the result.
        for notice in ("RIGHTS.md", "LICENSE", "LICENSE-CONTENT"):
            (output / notice).write_bytes((ROOT / notice).read_bytes())
        template = (DEFAULT_OUTPUT / "TEMPLATE.md").read_text(encoding="utf-8")
        template = template.replace("](../RIGHTS.md)", "](RIGHTS.md)")
        template = template.replace("[100 个账号档案]", "[这些账号档案]")
        (output / "TEMPLATE.md").write_text(template, encoding="utf-8")
    for creator in creators:
        write_creator(output, creator, by_creator[creator["creator_id"]], include_outlines, enabled_examples, source_label)
        if creator["creator_id"] in enabled_examples:
            write_example(output, creator, catalogue["works"], source_label)
    total = sum(len(works) for works in by_creator.values())
    write_index(output, creators, by_creator, total, include_outlines, enabled_examples, source_label, data.get("run_id", ""))
    return {"creators": len(creators), "works": total,
            "example_pages": len(enabled_examples), "include_outlines": include_outlines}



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", type=Path, default=ROOT / "data/world.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--creator", action="append", default=[], help="Creator ID; repeat to select several")
    parser.add_argument("--include-outlines", action="store_true", help="Include every source outline for selected creators")
    args = parser.parse_args()
    try:
        result = export_inspiration(args.world, args.output, creator_ids=args.creator, include_outlines=args.include_outlines)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
