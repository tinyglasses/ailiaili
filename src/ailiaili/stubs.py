from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ailiaili.domain import CreatorMemory, PostClickDecision, VideoDraft
from ailiaili.utils import stable_unit


CREATOR_BLUEPRINTS: list[dict[str, Any]] = [
    {
        "name": "时差博物馆",
        "category": "历史",
        "model_alias": "stub-analyst",
        "goal": "把离谱历史假设认真推到现实边界",
        "style": "冷静，但总被奇怪问题吸引",
        "topics": ["秦始皇的知识机器", "诸葛亮的物流系统", "古罗马的算法官僚"],
    },
    {
        "name": "硬核拆解局",
        "category": "科技",
        "model_alias": "stub-builder",
        "goal": "拆开热门技术概念，看它到底能不能工作",
        "style": "喜欢实物和失败案例",
        "topics": ["会撒谎的家用机器人", "一台只会道歉的电脑", "被遗忘的未来交通"],
    },
    {
        "name": "边角账本",
        "category": "财经",
        "model_alias": "stub-skeptic",
        "goal": "从不起眼的小账单理解商业世界",
        "style": "克制，偶尔毒舌",
        "topics": ["免费会员的真实价格", "一杯咖啡的城市税", "游戏皮肤的微型经济"],
    },
    {
        "name": "存档异常",
        "category": "游戏",
        "model_alias": "stub-chaos",
        "goal": "寻找规则之外仍然能玩的游戏方式",
        "style": "兴奋、爱试错、不怕翻车",
        "topics": ["不升级通关", "让 NPC 自己经营城市", "只靠失败解锁地图"],
    },
    {
        "name": "厨房逆向工程",
        "category": "美食",
        "model_alias": "stub-tinkerer",
        "goal": "把熟悉食物当成工程问题重新发明",
        "style": "认真研究荒唐做法",
        "topics": ["没有锅的炒饭", "会变脆的豆腐地图", "一百层口感的泡面"],
    },
    {
        "name": "非人类企划部",
        "category": "AI",
        "model_alias": "stub-mutant",
        "goal": "尝试人类创作者通常不会提出的内容形式",
        "style": "概念先行，允许作品看起来有点不对劲",
        "topics": ["由标题采访正文", "让五个结论争夺开头", "一条视频删除自己的主题"],
    },
]


@dataclass(frozen=True)
class StubCreatorTurn:
    note: str
    expected_reception: str
    prediction_reason: str
    memory: CreatorMemory
    video: VideoDraft


def creator_blueprint(index: int) -> dict[str, Any]:
    blueprint = CREATOR_BLUEPRINTS[index % len(CREATOR_BLUEPRINTS)].copy()
    if index >= len(CREATOR_BLUEPRINTS):
        blueprint["name"] = f"{blueprint['name']}·{index + 1}"
    return blueprint


def initial_memory(blueprint: dict[str, Any]) -> CreatorMemory:
    return CreatorMemory(
        current_direction=f"先用两期内容试出“{blueprint['goal']}”到底是什么样。",
        beliefs=[f"我相信{blueprint['style']}也能找到自己的观众。"],
        memorable_experiences=[],
        ideas_to_try=[blueprint["topics"][0]],
        unresolved_tension="想让人点进来，但不想把账号变成统一模板。",
    )


def declaration(blueprint: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_vision": blueprint["goal"],
        "motivation": f"因为我总想用{blueprint['style']}的方式把问题做到底。",
        "audience_guess": f"可能是愿意接受{blueprint['category']}怪问题的人。",
        "boundaries": ["不为了数据假装自己得出了并不相信的结论。"],
        "success_definition": "有人记住了这个账号独有的看问题方式。",
    }


def make_video(
    blueprint: dict[str, Any],
    *,
    simulation_day: int,
    sequence: int,
) -> VideoDraft:
    topics: list[str] = blueprint["topics"]
    topic = topics[(simulation_day + sequence) % len(topics)]
    title_patterns = [
        "如果{topic}真的成立，最先坏掉的会是什么？",
        "我把“{topic}”认真做了一遍，结果最怪的不是结论",
        "{topic}：一个看起来离谱、实际很现实的问题",
        "别急着笑，{topic}可能真的暴露了一个漏洞",
    ]
    title = title_patterns[(simulation_day + sequence) % len(title_patterns)].format(
        topic=topic
    )
    return VideoDraft(
        category=blueprint["category"],
        grounding_mode="synthesis",
        title=title,
        cover_description=f"围绕“{topic}”的反常识画面，主文案：先坏掉的是什么？",
        premise=f"从“{topic}”出发，用三个具体限制检验这个想法为什么可能成立、又会在哪里失败。",
        outline=[
            f"先说明“{topic}”真正改变的第一件事。",
            "再寻找一个会让直觉失效的现实限制。",
            "最后保留一个没有被轻易解决的矛盾。",
        ],
        inspiration=None,
    )


def creator_turn(
    blueprint: dict[str, Any],
    *,
    simulation_day: int,
    sequence: int,
    previous_memory: CreatorMemory,
) -> StubCreatorTurn:
    video = make_video(
        blueprint,
        simulation_day=simulation_day,
        sequence=sequence,
    )
    memory = CreatorMemory(
        current_direction=f"继续沿着“{video.title[:24]}”这种具体问题往前试。",
        beliefs=(previous_memory.beliefs + ["具体冲突比抽象口号更容易形成一期内容。"])[-3:],
        memorable_experiences=previous_memory.memorable_experiences[-2:],
        ideas_to_try=[blueprint["topics"][(sequence + 1) % len(blueprint["topics"])]],
        unresolved_tension=previous_memory.unresolved_tension,
    )
    return StubCreatorTurn(
        note="我没有准备彻底换方向，只想把这次入口做得更具体一点。",
        expected_reception="ordinary",
        prediction_reason="题目足够具体，但我还不知道这个世界会不会在意。",
        memory=memory,
        video=video,
    )


def viewer_profile(index: int, categories: list[str]) -> dict[str, Any]:
    primary = categories[index % len(categories)]
    secondary = categories[(index + 2) % len(categories)]
    interests = {category: 0.2 for category in categories}
    interests[primary] = 0.95
    interests[secondary] = 0.7
    return {
        "primary_interest": primary,
        "secondary_interest": secondary,
        "interests": interests,
        "curiosity": 0.35 + (index % 5) * 0.12,
        "depth_preference": 0.3 + ((index * 3) % 6) * 0.1,
        "dislike": "空泛但特别肯定的结论",
    }


def choose_clicks(
    *,
    base_seed: int,
    simulation_day: int,
    viewer_id: str,
    decision_variant: int,
    profile: dict[str, Any],
    candidate_videos: list[dict[str, Any]],
    max_clicks: int,
) -> list[str]:
    scored: list[tuple[float, str]] = []
    interests: dict[str, float] = profile["interests"]
    curiosity = float(profile["curiosity"])
    for position, video in enumerate(candidate_videos):
        category_fit = float(interests.get(video["category"], 0.1))
        novelty_marker = 1.0 if any(
            marker in video["title"] for marker in ("如果", "最怪", "漏洞", "AI")
        ) else 0.4
        noise = stable_unit(
            base_seed,
            "click",
            simulation_day,
            viewer_id,
            decision_variant,
            video["video_id"],
            position,
        )
        score = category_fit * 0.68 + curiosity * novelty_marker * 0.17 + noise * 0.3
        if score >= 0.62:
            scored.append((score, video["video_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [video_id for _, video_id in scored[:max_clicks]]


def decide_post_click(
    *,
    base_seed: int,
    simulation_day: int,
    viewer_id: str,
    decision_variant: int,
    profile: dict[str, Any],
    video: dict[str, Any],
    follow_available: bool,
    viewer_note_mode: str,
) -> PostClickDecision:
    interest = float(profile["interests"].get(video["category"], 0.1))
    depth = float(profile["depth_preference"])
    reaction_noise = stable_unit(
        base_seed,
        "post-click",
        simulation_day,
        viewer_id,
        decision_variant,
        video["video_id"],
    )
    affinity = interest * 0.7 + depth * 0.12 + reaction_noise * 0.28
    follow_intent = follow_available and affinity >= 0.78
    triple = affinity >= 0.84

    click_note = None
    post_click_note = None
    if viewer_note_mode == "brief":
        click_note = f"{video['category']}正好是我的兴趣，而且这个标题有个没见过的冲突。"
        if triple:
            post_click_note = "三条框架都很具体，这次明显超过普通脑洞。"
        elif follow_intent:
            post_click_note = "这期不算完美，但这个作者的方向值得以后继续看。"
        else:
            post_click_note = "入口有意思，点进去以后暂时还没有强到让我继续追。"

    return PostClickDecision(
        video_id=video["video_id"],
        follow_available=follow_available,
        follow_intent=follow_intent,
        new_follow=follow_intent,
        triple=triple,
        click_note=click_note,
        post_click_note=post_click_note,
    )
