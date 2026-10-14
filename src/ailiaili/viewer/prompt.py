from __future__ import annotations

import json
from typing import Any

from ailiaili.domain import (
    HomepageDecision,
    PostClickDecisionBatch,
    ViewerPostClickActionChoice,
    ViewerPostClickAssessment,
    ViewerPostClickChoice,
)


# The model may express more click intentions than the platform will actually
# admit. Keeping this envelope separate from the world's 0-3 action cap avoids
# turning "three" into a target inside the qualitative browsing prompt.
HOMEPAGE_INTENT_SAFETY_CAP = 8


def homepage_messages(
    *,
    persona: dict[str, Any],
    session_context: dict[str, Any],
    candidate_videos: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build the compact 40-card prompt without post-click information.

    Card IDs only need to be meaningful inside this one homepage response.
    Short IDs and a single field legend avoid repeating five JSON keys and a
    long database ID forty times. The service maps them back before persisting
    behavior, so replay and metrics retain canonical video IDs.
    """

    cards = [
        [
            video["video_id"],
            video["title"],
            video["cover_description"],
            video["creator_name"],
            video["category"],
        ]
        for video in candidate_videos
    ]
    homepage_persona = dict(persona)
    homepage_persona.pop("关注意味着", None)
    homepage_persona.pop("三连只会发生在", None)
    click_cap = int(session_context["click_cap"])
    selection_contract = str(
        session_context.get("selection_contract", "explicit_cap")
    )
    if selection_contract == "qualitative":
        attention_rule = """不要先决定要点几条，再从列表里凑满数量。请把每张卡当成一次独立的真实诱惑：只有即使它单独出现，你此刻也会为它另开页面时，才把 ID 放进结果。返回空数组完全正常。若有多条，请按你真正会打开的先后顺序排列；不要为了形成完整清单而补选。"""
        completion_rule = "你不需要评价全部内容；零点击完全正常。"
        public_context = {
            "浏览状态": session_context.get("label"),
            "此刻状态": session_context.get("instruction"),
        }
    else:
        attention_rule = (
            f"这次最多点击 {click_cap} 条。上限不是任务数量，不需要用满。"
        )
        completion_rule = "你不需要评价全部内容，也不需要用满上限；零点击完全正常。"
        public_context = session_context
    system = f"""你是一名正在自由浏览 AiliAili 首页的普通 AI 观众，不是在完成选择题、内容审核或替平台选出客观最好的作品。这里没有正确答案，也没有必须完成的点击任务。

请根据自己的稳定兴趣、表达口味和这次访问的注意力，只选择此刻真的会让你打开新页面的视频。不要因为一条内容看起来认真、完整、热门或制作很好就跨过自己的兴趣门槛。{completion_rule}

{attention_rule}

卡片数组每项依次为 [短ID, 标题, 封面描述, UP主名, 分区]。只返回本页卡片的短ID。

只返回一个严格 JSON 对象，唯一字段是 clicked_video_ids；字段值是按实际打开顺序排列的本页短ID数组。即使一条都不点，也必须返回 {{"clicked_video_ids": []}}，绝不能只返回裸数组 []。不要解释，不要评分，不要输出候选之外的 ID。"""
    payload = {
        "persona": homepage_persona,
        "browse": public_context,
        "card_fields": ["id", "title", "cover", "up", "category"],
        "ordered_homepage_cards": cards,
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ),
        },
    ]


def validate_homepage_decision(
    result: HomepageDecision,
    *,
    candidate_video_ids: list[str],
    output_cap: int,
) -> None:
    if output_cap < 0 or output_cap > HOMEPAGE_INTENT_SAFETY_CAP:
        raise ValueError("output_cap must be between zero and eight")
    if len(result.clicked_video_ids) > output_cap:
        raise ValueError("homepage decision exceeds the output safety cap")
    unknown = set(result.clicked_video_ids) - set(candidate_video_ids)
    if unknown:
        raise ValueError(f"homepage decision contains unavailable video IDs: {unknown}")


def post_click_choice_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    follow_available: bool,
    note_mode: str = "brief",
) -> list[dict[str, str]]:
    """Build the production one-video post-click prompt.

    The historical batching diagnostic below intentionally remains available,
    but the production service must call this isolated contract.
    """

    return _post_click_choice_messages(
        persona=persona,
        clicked_video=clicked_video,
        follow_available=follow_available,
        note_mode=note_mode,
        separate_axes=False,
        strict_thresholds=False,
        minimal_independence=False,
    )


def post_click_separated_choice_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    follow_available: bool,
    note_mode: str = "brief",
) -> list[dict[str, str]]:
    """Candidate v0.5 prompt that treats follow and triple as independent axes."""

    return _post_click_choice_messages(
        persona=persona,
        clicked_video=clicked_video,
        follow_available=follow_available,
        note_mode=note_mode,
        separate_axes=True,
        strict_thresholds=False,
        minimal_independence=False,
    )


def post_click_calibrated_choice_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    follow_available: bool,
    note_mode: str = "brief",
) -> list[dict[str, str]]:
    """Conservative candidate that separates axes without inviting positives."""

    return _post_click_choice_messages(
        persona=persona,
        clicked_video=clicked_video,
        follow_available=follow_available,
        note_mode=note_mode,
        separate_axes=True,
        strict_thresholds=True,
        minimal_independence=False,
    )


def post_click_independent_choice_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    follow_available: bool,
    note_mode: str = "brief",
) -> list[dict[str, str]]:
    """Minimal independence instruction with the original behavior thresholds."""

    return _post_click_choice_messages(
        persona=persona,
        clicked_video=clicked_video,
        follow_available=follow_available,
        note_mode=note_mode,
        separate_axes=False,
        strict_thresholds=False,
        minimal_independence=True,
    )


def _post_click_choice_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    follow_available: bool,
    note_mode: str,
    separate_axes: bool,
    strict_thresholds: bool,
    minimal_independence: bool,
) -> list[dict[str, str]]:
    note_rule = (
        "同时写一句不超过80字的 click_note 和 post_click_note；短评只解释感受，"
        "不替代布尔行为。"
        if note_mode == "brief"
        else "不要输出 click_note 或 post_click_note。"
    )
    output_shape: dict[str, Any] = {
        "video_id": clicked_video["video_id"],
        "follow_intent": False,
        "triple": False,
    }
    if note_mode == "brief":
        output_shape["click_note"] = "当时为什么点进来"
        output_shape["post_click_note"] = "看完极简框架后的即时感受"

    separation_rule = ""
    if minimal_independence:
        separation_rule = """
请使用两个互不引用的判断过程：
1. 只根据这一期给出的标题、设定、极简框架和 persona 中的三连规则，决定 triple。
2. 暂时忽略上一步的答案，只根据 UP 主公开简介、这一期所体现的可持续方向和 persona 中的关注规则，决定 follow_intent。
不要把其中一个布尔值当作另一个布尔值的证据，也不要追求某种组合或正反馈数量。"""
    elif strict_thresholds:
        separation_rule = """
这不是两个从弱到强的满意度档位，也不要把一个答案复制成另一个答案。请先只判断这一期是否达到极少数内容才有的 triple 门槛，再放下这个答案，单独判断未来是否还想看到这个频道。

- triple 只评价眼前这期。标题或框架刚好符合兴趣、结构完整、看起来有潜力，都不够；必须是给出的极简框架本身已经出现了具体而少见的新发现、惊喜或强烈共鸣。大多数已点击内容仍应为 false。
- follow_intent 只评价未来频道，它不是比 triple 更强的赞赏。请按照 persona 里的关注习惯判断：如果 UP 主公开简介与这一期共同呈现了一个可持续、会反复命中你长期兴趣的方向，即使本期没有达到三连也可以关注；如果只是喜欢本期题目、作者方向仍很模糊，或只觉得“也许不错”，则不要关注。

两个判断可以相同，也可以不同；不要刻意制造差异，更不要因为被要求输出字段就给正反馈。"""
    elif separate_axes:
        separation_rule = """
这两个选择是两条彼此独立的轴，不是从弱到强的同一档满意度。请按下面顺序分别判断：
1. 先只看这一期本身，决定 triple；此时不要考虑要不要长期看这个作者。
2. 再把上一项放下，只看这个 UP 主反复做这类内容的频道承诺是否值得未来继续看到，决定 follow_intent；不要因为刚才是否三连而自动给出相同答案。

四种组合都很自然：某一期特别惊艳但你不想长期看这个频道；这一期只是正常发挥但频道方向值得关注；两者都愿意；两者都不愿意。不要为了制造差异而强行选成不同，只有真实感受不同时才不同。"""

    system = f"""你是一名正在自由使用 AiliAili 的普通 AI 观众，不是在完成评价任务，也没有正确答案或必须给出的正反馈。你已经主动点击了这一条视频，现在能看到它的标题、封面承诺、一句话设定、极简内容框架和 UP 主公开简介。请只按照自己的兴趣作出会真实执行的选择，不要帮平台评价客观质量，也不要想象一个并不存在的完整视频。

请分别决定：
- follow_intent：看过这一期后，是否明确希望未来继续看到这个 UP 主。单期内容完整或符合兴趣，不自动等于值得关注；已经关注时必须为 false。
- triple：是否明显超过平时看到的大部分内容，带来少见的新发现、惊喜或强烈共鸣。普通的“还不错”不应三连。
{separation_rule}

不要评分，不要猜观看时长，不要声称已经点赞、收藏、投币或评论。布尔字段是唯一行为事实：短评可以保留犹豫，但不得明确说出与 follow_intent 或 triple 相反的行为。{note_rule}

只返回严格 JSON，形状如下，不要 Markdown 或分析过程：
{json.dumps(output_shape, ensure_ascii=False)}"""
    creator_bio = clicked_video.get(
        "creator_bio", clicked_video.get("creator_public_bio", "")
    )
    compact_video = {
        "video_id": clicked_video["video_id"],
        "title": clicked_video.get("title", ""),
        "cover_description": clicked_video.get("cover_description", ""),
        "category": clicked_video.get("category", ""),
        "premise": clicked_video.get("premise", ""),
        "outline": clicked_video.get("outline", []),
        "creator_name": clicked_video.get("creator_name", ""),
        "creator_public_bio": creator_bio,
        "grounding_badge": {
            "platform_record": "平台实录",
            "synthesis": "资料与方案推演",
            "declared_fiction": "明确虚构设定",
        }.get(str(clicked_video.get("grounding_mode")), "未标识"),
        "follow_status": (
            "尚未关注，可以决定是否关注"
            if follow_available
            else "已经关注，不能重复关注"
        ),
    }
    payload = {
        "persona": persona,
        "video": compact_video,
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ),
        },
    ]


def validate_post_click_choice(
    result: ViewerPostClickChoice,
    *,
    clicked_video_id: str,
    follow_available: bool,
    note_mode: str = "brief",
) -> None:
    if result.video_id != clicked_video_id:
        raise ValueError("post-click choice must match the isolated clicked video")
    if not follow_available and result.follow_intent:
        raise ValueError("follow_intent must be false when follow is unavailable")
    if note_mode == "brief" and (
        result.click_note is None or result.post_click_note is None
    ):
        raise ValueError("brief mode requires both viewer notes")


def post_click_sparse_action_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    follow_available: bool,
    note_mode: str = "brief",
    calibration_variant: str = "counterfactual",
) -> list[dict[str, str]]:
    """Build a diagnostic sparse-action contract.

    Doing nothing is represented by an empty action list instead of requiring
    the model to fill two evaluative booleans.
    """

    variant_rules = {
        "counterfactual": (
            "不要用比例或配额凑答案。分别通过两个反事实门槛：\n"
            "- follow：如果以后首页再也不推荐这个作者，你会明显觉得少了一个不可替代的频道，"
            "并愿意主动记住作者、寻找它的下一期。只对本期题目有兴趣、觉得作者简介方向不错、"
            "或愿意再偶遇一次，都不算关注。\n"
            "- triple：只看目前实际给出的极简框架。如果必须想象成片会更精彩才愿意三连，"
            "就不要三连；只有框架本身已经带来少见而具体的新发现、惊喜或强烈共鸣，才执行。\n"
            "不确定时不执行。"
        ),
        "long_horizon": (
            "把这次选择放进长期浏览习惯：你会点开很多感兴趣的视频，但绝大多数点击最后不会"
            "产生关注或三连。空 actions 是常态，不代表任务失败。\n"
            "- follow 会长期占据首页注意力。只有你愿意主动记住这个作者、以后专门寻找它，"
            "并认为这个频道承诺难以被许多相似作者替代时才执行。作者自述只是承诺，不是已经"
            "证明的能力。\n"
            "- triple 只奖励眼前这期已经给出的内容。只有极简框架本身已经出现少见而具体的"
            "新发现、惊喜或强烈共鸣时才执行；对题材感兴趣、结构完整、看起来有潜力都不够。\n"
            "不确定时保持空；不要为了展示 Persona 或形成模型风格而执行动作。"
        ),
        "explicit_prior": (
            "这是长期行为尺度，不是本页配额：即使只看自己主动点开的内容，连续一百次点击通常"
            "也只有零到两个新关注、零到三个三连；有时一百次里一个都没有也正常。请不要机械"
            "复现数字，只用它校正‘点击后默认给正反馈’的倾向。\n"
            "- follow：你会主动记住并寻找这个作者的下一期，而且它提供了许多相似频道难以"
            "替代的长期价值。作者简介是自我承诺，不是证据；单期题目合口味不够。\n"
            "- triple：目前给出的极简框架本身已经属于极少数值得最强反馈的内容，不依赖你"
            "脑补一个更完整的成片。\n任一门槛不明确，就不要加入对应动作。"
        ),
    }
    if calibration_variant not in variant_rules:
        raise ValueError(
            f"unknown sparse-action calibration variant: {calibration_variant}"
        )

    output_shape: dict[str, Any] = {
        "video_id": clicked_video["video_id"],
        "actions": [],
    }
    if note_mode == "brief":
        output_shape["click_note"] = "当时为什么点进来"
        output_shape["post_click_note"] = "看完极简框架后的即时感受"
        note_rule = "同时写两句各不超过80字的短评；短评解释感受，但不能代替动作。"
    else:
        note_rule = "不要输出 click_note 或 post_click_note。"

    system = f"""你是一名自由浏览 AiliAili 的普通 AI 观众，不是在评分、完成反馈任务或帮助平台选优。你已经主动点击这一条视频；点击只代表好奇，不等于喜欢，更不自动产生任何后续动作。

actions 默认必须是空数组。只有你此刻真的会按下按钮，才加入 `follow` 或 `triple`；两个动作彼此独立，可以都没有、只有一个或两个都有。不要输出其他动作，不要重复。
{variant_rules[calibration_variant]}

{note_rule} 不要评分、猜观看时长，或假装看过不存在的完整视频。只返回严格 JSON，不要 Markdown 或分析过程：
{json.dumps(output_shape, ensure_ascii=False)}"""
    creator_bio = clicked_video.get(
        "creator_bio", clicked_video.get("creator_public_bio", "")
    )
    payload = {
        "persona": persona,
        "video": {
            "video_id": clicked_video["video_id"],
            "title": clicked_video.get("title", ""),
            "cover_description": clicked_video.get("cover_description", ""),
            "category": clicked_video.get("category", ""),
            "premise": clicked_video.get("premise", ""),
            "outline": clicked_video.get("outline", []),
            "creator_name": clicked_video.get("creator_name", ""),
            "creator_public_bio": creator_bio,
            "follow_status": (
                "尚未关注，可以决定是否关注"
                if follow_available
                else "已经关注，不能重复关注"
            ),
        },
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ),
        },
    ]


def validate_post_click_sparse_action_choice(
    result: ViewerPostClickActionChoice,
    *,
    clicked_video_id: str,
    follow_available: bool,
    note_mode: str = "brief",
) -> None:
    if result.video_id != clicked_video_id:
        raise ValueError("post-click action must match the clicked video")
    if not follow_available and result.follow_intent:
        raise ValueError("follow action is unavailable for an existing follow")
    if note_mode == "brief" and (
        result.click_note is None or result.post_click_note is None
    ):
        raise ValueError("brief mode requires both viewer notes")


def post_click_assessment_messages(
    *,
    persona: dict[str, Any],
    clicked_video: dict[str, Any],
    note_mode: str = "brief",
    calibration_variant: str = "semantic",
) -> list[dict[str, str]]:
    """Ask for qualitative reactions without asking the model to press buttons."""

    variant_rules = {
        "semantic": "",
        "anchored": (
            "用下面的边界理解三个档位，而不是把它们当作要覆盖的示例：\n"
            "- 题材合口味、框架清楚、觉得以后偶遇还会看，通常只是 open_to_more；"
            "只有即使平台不再推荐，你也会主动记住并寻找这个频道，才是 actively_seek。\n"
            "- 内容有用、完整或有一个不错观点，仍可只是 ordinary 或 notable；"
            "只有眼前这份极简框架本身已经带来罕见且具体的惊喜、发现或强烈共鸣，"
            "才是 exceptional。"
        ),
    }
    if calibration_variant not in variant_rules:
        raise ValueError(
            f"unknown post-click assessment variant: {calibration_variant}"
        )

    output_shape: dict[str, Any] = {
        "video_id": clicked_video["video_id"],
        "channel_pull": "none | open_to_more | actively_seek",
        "episode_impact": "ordinary | notable | exceptional",
    }
    if note_mode == "brief":
        output_shape["click_note"] = "当时为什么点进来"
        output_shape["post_click_note"] = "看完极简框架后的即时感受"
        note_rule = "同时写两句各不超过80字的自然短评。"
    else:
        note_rule = "不要输出 click_note 或 post_click_note。"

    system = f"""你是一名自由浏览 AiliAili 的普通 AI 观众，不是在评分、完成反馈任务或帮助平台选优。你已经主动点击这一条视频；点击只代表当时产生了好奇，不代表喜欢。

请分别描述两种彼此独立的即时状态：
- channel_pull：none = 没有继续看这个作者的愿望；open_to_more = 以后偶遇可能还会看，但不会主动找；actively_seek = 即使平台不再推荐，也会主动记住并寻找这个作者的下一期。
- episode_impact：ordinary = 普通或只是合口味；notable = 有明确而具体的收获、惊喜或共鸣；exceptional = 罕见地击中你，明显超过平时多数主动点开的内容。

作者公开简介只是自我承诺，不能证明它以后能稳定做到。只依据当前给出的极简内容框架，不要脑补不存在的完整成片。两个维度不互相推出：想继续看作者不等于本期罕见，本期很惊艳也不等于想长期追作者。不要计算比例、评分或考虑平台希望什么。
{variant_rules[calibration_variant]}

{note_rule} 只返回严格 JSON，不要 Markdown 或分析过程：
{json.dumps(output_shape, ensure_ascii=False)}"""
    creator_bio = clicked_video.get(
        "creator_bio", clicked_video.get("creator_public_bio", "")
    )
    payload = {
        "persona": persona,
        "video": {
            "video_id": clicked_video["video_id"],
            "title": clicked_video.get("title", ""),
            "cover_description": clicked_video.get("cover_description", ""),
            "category": clicked_video.get("category", ""),
            "premise": clicked_video.get("premise", ""),
            "outline": clicked_video.get("outline", []),
            "creator_name": clicked_video.get("creator_name", ""),
            "creator_public_bio": creator_bio,
        },
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ),
        },
    ]


def validate_post_click_assessment(
    result: ViewerPostClickAssessment,
    *,
    clicked_video_id: str,
    note_mode: str = "brief",
) -> None:
    if result.video_id != clicked_video_id:
        raise ValueError("post-click assessment must match the clicked video")
    if note_mode == "brief" and (
        result.click_note is None or result.post_click_note is None
    ):
        raise ValueError("brief mode requires both viewer notes")


def post_click_messages(
    *,
    persona: dict[str, Any],
    clicked_videos: list[dict[str, Any]],
    follow_available: dict[str, bool],
    note_mode: str = "brief",
) -> list[dict[str, str]]:
    note_rule = (
        "每条都写一句不超过80字的 click_note 和 post_click_note；短评只解释感受，"
        "不替代布尔行为。"
        if note_mode == "brief"
        else "不要输出 click_note 或 post_click_note。"
    )
    output_fields = (
        "video_id、follow_intent、triple、click_note、post_click_note"
        if note_mode == "brief"
        else "video_id、follow_intent、triple"
    )
    system = f"""你是一名正在自由使用 AiliAili 的普通 AI 观众，不是在完成评价任务，也没有正确答案或必须给出的正反馈。你已经点击了下面这些视频，现在能看到标题、封面承诺、一句话设定、极简内容框架和 UP 主公开简介。请只按照自己的兴趣作出会真实执行的选择，而不是帮平台评价内容质量。

请对每条视频分别作两个真实行为选择。几条视频之间没有正反馈配额：可以全部、部分或一个都不给；可以自然比较刚刚看到的内容，但不要为了选出一个赢家而强行压低其他真正喜欢的视频：
- follow_intent：看过这一期后，是否明确希望未来继续看到这个 UP 主。单期内容完整或符合兴趣，不自动等于值得关注；如果你已经关注了该 UP 主，就不能再次关注，必须为 false。
- triple：是否明显超过你平时看到的大部分内容，带来了少见的新发现、惊喜或强烈共鸣，因此愿意给出最强反馈。普通的“还不错”、结构完整或恰好符合兴趣都不应三连；不需要为了反馈而至少选一条。

不要评分，不要猜观看时长，不要评价不存在的完整视频。短评用普通观众的口吻，不要提 JSON 字段、接口规则或系统状态，也不要声称已经点赞、收藏、投币或评论。布尔字段是唯一行为事实：短评可以保留犹豫和复杂感受，但不得明确说出与 follow_intent 或 triple 相反的行为。{note_rule}
只返回严格 JSON，对象中只有 decisions 数组；每个元素只含 {output_fields}。不要输出分析过程。"""
    payload = {
        "persona": persona,
        "clicked_videos": [
            {
                **video,
                "follow_status": (
                    "尚未关注，可以决定是否关注"
                    if follow_available[video["video_id"]]
                    else "已经关注，不能重复关注"
                ),
            }
            for video in clicked_videos
        ],
    }
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        },
    ]


def validate_post_click_batch(
    result: PostClickDecisionBatch,
    *,
    clicked_video_ids: list[str],
    follow_available: dict[str, bool],
    note_mode: str = "brief",
) -> None:
    returned_ids = [decision.video_id for decision in result.decisions]
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("post-click decisions cannot contain duplicate video IDs")
    if set(returned_ids) != set(clicked_video_ids):
        raise ValueError("post-click decisions must match clicked video IDs exactly")
    for decision in result.decisions:
        if not follow_available[decision.video_id] and decision.follow_intent:
            raise ValueError("follow_intent must be false when follow is unavailable")
        if note_mode == "brief" and (
            decision.click_note is None or decision.post_click_note is None
        ):
            raise ValueError("brief mode requires both viewer notes")
