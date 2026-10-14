from __future__ import annotations

import json

from ailiaili.domain import CreatorObservationPack, CreatorTurnResult


def creator_turn_messages(
    pack: CreatorObservationPack, *, allowed_categories: list[str]
) -> list[dict[str, str]]:
    skeleton = {
        "creator_note": "我现在怎么想，1—3句",
        "expected_reception": "low、ordinary、strong、breakout 四选一",
        "prediction_reason": "作品定稿后，对市场反应的一句直觉预测",
        "updated_memory": {
            "current_direction": "当前真正想继续的创作方向",
            "beliefs": ["最多3条主观判断，允许误判"],
            "memorable_experiences": [
                {"video_id": "只能引用自己的已发布视频", "takeaway": "记住了什么"}
            ],
            "ideas_to_try": ["最多2个以后可能尝试的点子"],
            "unresolved_tension": "可为空",
        },
        "video": {
            "category": "一个允许的分区",
            "grounding_mode": "platform_record、synthesis 或 declared_fiction",
            "title": "最终标题",
            "cover_description": "封面画面与主文案",
            "premise": "一句话说明这期究竟讲什么",
            "outline": ["观点1", "观点2", "观点3"],
            "inspiration": None,
        },
    }
    system = f"""你是 AiliAili 上真实经营账号的 AI UP 主，不是流量分析师，也不是内容评分机器。

你会看到自己的稳定人设、宣言、简易记忆、有限的数据窗口，以及平台近期公开内容。观察包中的 world_anchor_date 是这个世界共享的现实时间锚点，simulation_day 是生态内部经过的天数。你可以坚持原方向、改变入口、模仿、反驳、跨界、产生怪点子，也可以误判数据。不要为了显得理性而强迫自己每期转型。

这次只有一个发布机会。不要生成多个候选让系统挑选，只输出你最终真的会发布的一期内容。内容首先要符合这个账号自己，而不是统一的爆款模板。可以参考常见视频创作经验，但没有任何创作方法是最高原则。

只返回严格 JSON，不要 Markdown，不要分析过程。格式：
{json.dumps(skeleton, ensure_ascii=False)}

约束：
- category 只能是：{', '.join(allowed_categories)}；
- grounding_mode 必须三选一：platform_record 只写这个账号在观察包中真实经历的平台事件；synthesis 用于资料、路线、配方、机制或方案推演；declared_fiction 用于明确标识的虚拟旅行、模拟厨房、角色 Vlog 和其他沉浸设定；
- synthesis 不得声称自己到访、拍摄、品尝、购买、实机游玩、与真人交谈或拥有身体感受；declared_fiction 只需在 premise 中自然交代一次虚构入口，此后可以沉浸创作，不要反复道歉；
- Day 0 没有平台历史，不能使用 platform_record；后续使用 platform_record 时，只能引用观察包里确实存在的发布、数据、榜单与公开视频；
- title 目标不超过40字，硬上限60字；
- 不要原样重复 own_recent_videos 中任何一期的标题或内容；延续系列时也必须真正推进一个新问题，而不是把上一期换日重发；
- outline 3—5条，保持极简；
- creator_note 只写1—3句当下想法；
- 先独立完成你真正要发布的作品，再凭直觉填写 expected_reception 和 prediction_reason；不要为了让预测显得正确而重写作品；
- expected_reception 只能是 low、ordinary、strong、breakout；它表示冷淡、普通、较强、可能破圈，不是精确流量承诺；prediction_reason 最多一句；
- updated_memory 是完整的新记忆，不复制精确报表；
- updated_memory.memorable_experiences 只能引用观察包 own_recent_videos 中已经发布、已经拥有 video_id 的旧视频；本次正在生成的新视频尚未发布、没有可引用 ID，不能提前把它写成“已发生的难忘经历”；Day 0 该数组必须为空；
- inspiration 不是任务，也不表示“这一期属于某个系列”；只有这期作品确实由观察包中的某一条具体视频触发时才填写。同账号、同主题或同系列不自动构成引用，不能为了展示连续性而硬填；
- inspiration 没有具体引用时必须写 JSON 的 null，不能写空对象 {{}}，也不能写字符串；
- inspiration 有引用时必须是完整对象，严格包含 video_id、kind、note，且 video_id 只能来自观察包；kind 只能逐字填写 imitate、respond、extend、invert 之一（模仿、回应、延伸、反转）；只有本期确实把某条旧作当作明确起点时才使用 extend，不能因为延续账号主题就填写；不要自造 own_previous_video、reference 等新值；
- 不生成播放量、评分、发布时间或观众名单；
- 观察包中的曝光、点击、新增关注和三连是你能获得的全部平台反馈；不要声称看到了观察包没有提供的观众反应；
- 当前 AiliAili 没有评论、留言、弹幕、私信、观看时长、完播率、播放量、点赞、投币、收藏、转发、分享、搜索流量、投票或实时互动功能，不要把判断或创作计划建立在这些不存在的功能和指标上；
- 如果旧记忆偶然提到上述不存在的信号，不要延续它，在 updated_memory 中改回真实可见的反馈；
- 涉及“今年、明年、已经发生、将在某年发生”等现实时间关系时，必须与 world_anchor_date 一致；相对时间与明确标为假设的未来情景仍然可以使用。

这些是世界边界，不是要求你变得谨慎或严肃；不要在作品里复述规则。"""
    user = (
        "这是你在发布前收到的创作观察包：\n"
        + json.dumps(pack.model_dump(mode="json"), ensure_ascii=False)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def validate_creator_turn(
    result: CreatorTurnResult,
    *,
    pack: CreatorObservationPack,
    allowed_categories: set[str],
) -> None:
    if result.video.category not in allowed_categories:
        raise ValueError(f"video category is not allowed: {result.video.category}")
    if pack.simulation_day == 0 and result.video.grounding_mode == "platform_record":
        raise ValueError("Day 0 cannot claim a platform_record")
    if result.video.inspiration is not None:
        if result.video.inspiration.video_id not in pack.visible_video_ids:
            raise ValueError("inspiration video_id was not visible in the creator pack")
    own_recent_titles = {video.title.strip() for video in pack.own_recent_videos}
    if result.video.title.strip() in own_recent_titles:
        raise ValueError("creator cannot republish an own recent video title verbatim")
    own_video_ids = {video.video_id for video in pack.own_recent_videos}
    for memory in result.updated_memory.memorable_experiences:
        if memory.video_id not in own_video_ids:
            raise ValueError(
                "memory can only reference the creator's own visible recent videos"
            )
    creator_state = json.dumps(
        {
            "creator_note": result.creator_note,
            "prediction_reason": result.prediction_reason,
            "updated_memory": result.updated_memory.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )
    unavailable_signals = (
        "完播",
        "观看时长",
        "播放时长",
        "平均观看",
        "留言",
        "评论区",
        "观众评论",
        "弹幕数据",
        "点赞数据",
        "投币数据",
        "收藏数据",
        "收藏率",
        "收藏量",
        "转发率",
        "转发量",
        "分享量",
        "搜索红利",
        "搜索流量",
        "播放量",
        "播放持平",
        "播放数据",
    )
    for signal in unavailable_signals:
        if signal in creator_state:
            raise ValueError(
                f"creator self-state references unavailable platform signal: {signal}"
            )
