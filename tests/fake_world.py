from __future__ import annotations
import json, threading, time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any
from ailiaili.config import SimulationConfig
from ailiaili.llm.config import LLMBudget, LLMRuntimeConfig, ModelPrice, ModelSpec
from ailiaili.llm.provider import ProviderResult, ProviderUsage
ROOT=Path(__file__).resolve().parents[1]


class FakeWorldProvider:
    def __init__(
        self, *, delay_seconds: float = 0, homepage_click_count: int = 1
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.delay_seconds = delay_seconds
        self.homepage_click_count = homepage_click_count
        self.max_active_calls = 0
        self.max_active_by_kind: dict[str, int] = Counter()
        self._active_by_kind: dict[str, int] = Counter()
        self._active_calls = 0
        self._lock = threading.Lock()
        self.creator_homepage_overlapped = False

    def complete(self, **kwargs: Any) -> ProviderResult:
        with self._lock:
            self.calls.append(kwargs)
            call_number = len(self.calls)
            self._active_calls += 1
            self.max_active_calls = max(
                self.max_active_calls, self._active_calls
            )
        messages = kwargs["messages"]
        system = messages[0]["content"]
        user = messages[1]["content"]
        if "真实经营账号的 AI UP 主" in system:
            call_kind = "creator"
        elif "ordered_homepage_cards" in user:
            call_kind = "homepage"
        else:
            call_kind = "post_click"
        with self._lock:
            self._active_by_kind[call_kind] += 1
            self.max_active_by_kind[call_kind] = max(
                self.max_active_by_kind[call_kind],
                self._active_by_kind[call_kind],
            )
            if (
                self._active_by_kind["creator"] > 0
                and self._active_by_kind["homepage"] > 0
            ):
                self.creator_homepage_overlapped = True
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        if call_kind == "creator":
            pack = json.loads(user[user.index("{") :])
            day = int(pack["simulation_day"])
            creator_id = str(pack["creator_id"])
            content = json.dumps(
                {
                    "creator_note": "先沿着自己的兴趣继续做，再看看观众是否愿意留下。",
                    "expected_reception": "ordinary",
                    "prediction_reason": "The entry is concrete, but the audience fit is uncertain.",
                    "updated_memory": {
                        "current_direction": f"继续做第 {day} 天真正想做的具体问题。",
                        "beliefs": ["具体入口比空泛口号更像我。"],
                        "memorable_experiences": [],
                        "ideas_to_try": ["下一次换一个更意外的切口。"],
                        "unresolved_tension": "既想被看到，也不想变成模板。",
                    },
                    "video": {
                        "category": pack["primary_category"],
                        "grounding_mode": "synthesis",
                        "title": f"{creator_id} 第{day}天的怪问题",
                        "cover_description": "大字：这件小事为什么会突然失控？",
                        "premise": "从一个具体反常识问题出发，看看直觉在哪一步失效。",
                        "outline": [
                            "先给出最直觉的答案。",
                            "放进一个会改变结论的限制。",
                            "留下一个没被轻易解决的矛盾。",
                        ],
                        "inspiration": None,
                    },
                },
                ensure_ascii=False,
            )
        elif call_kind == "homepage":
            payload = json.loads(user)
            cards = payload["ordered_homepage_cards"]
            click_cap = int(
                payload.get("browse", {}).get(
                    "click_cap", self.homepage_click_count
                )
            )
            content = json.dumps(
                {
                    "clicked_video_ids": (
                        [
                            (card[0] if isinstance(card, list) else card["video_id"])
                            for card in cards[
                                : min(self.homepage_click_count, click_cap)
                            ]
                        ]
                    )
                }
            )
        else:
            payload = json.loads(user)
            video_id = payload["video"]["video_id"]
            result: dict[str, Any] = {
                "video_id": video_id,
                "channel_pull": "open_to_more",
                "episode_impact": "notable",
            }
            if '"click_note"' in system:
                result["click_note"] = "标题正好碰到我现在的兴趣。"
                result["post_click_note"] = "框架有意思，但还没强到三连。"
            content = json.dumps(result, ensure_ascii=False)

        usage = ProviderUsage(
            input_tokens=200,
            cached_input_tokens=0,
            output_tokens=80,
            reasoning_tokens=(40 if kwargs["reasoning_enabled"] else 0),
            raw={"prompt_tokens": 200, "completion_tokens": 80},
        )
        result = ProviderResult(
            content=content,
            request_id=f"fake-{call_number}",
            resolved_model="test/fake",
            usage=usage,
            raw={"content": content, "usage": usage.raw},
        )
        with self._lock:
            self._active_calls -= 1
            self._active_by_kind[call_kind] -= 1
        return result


def dual_model_runtime() -> LLMRuntimeConfig:
    return LLMRuntimeConfig(
        models={
            "fake_usd": ModelSpec(
                model_id="test/fake-usd",
                pricing=ModelPrice(
                    currency="USD",
                    input_per_million=Decimal("0.1"),
                    cached_input_per_million=Decimal("0.01"),
                    output_per_million=Decimal("0.5"),
                ),
            ),
            "fake_cny": ModelSpec(
                model_id="test/fake-cny",
                pricing=ModelPrice(
                    currency="CNY",
                    input_per_million=Decimal("0.2"),
                    cached_input_per_million=Decimal("0.04"),
                    output_per_million=Decimal("0.8"),
                ),
            ),
        },
        budget=LLMBudget(
            hard_limit=Decimal("1"),
            max_cost_per_call=Decimal("0.1"),
            hard_limit_by_currency={"USD": Decimal("1"), "CNY": Decimal("1")},
            max_cost_per_call_by_currency={
                "USD": Decimal("0.1"),
                "CNY": Decimal("0.1"),
            },
            max_attempts_per_call=2,
        ),
    )


def dual_model_config(
    tmp_path: Path, *, run_id: str, max_concurrency: int
) -> SimulationConfig:
    return SimulationConfig(
        run_id=run_id,
        seed=20260811,
        world_anchor_date="2026-08-11",
        days=1,
        creator_count=4,
        viewer_count=4,
        homepage_size=4,
        max_clicks=3,
        publish_interval_days=1,
        initial_videos_per_creator=1,
        cold_impression_target=2,
        exploit_ratio=0.5,
        cold_ratio=0.25,
        explore_ratio=0.25,
        max_videos_per_creator=1,
        age_half_life_days=14,
        viewer_note_mode="brief",
        random_visitor_note_sample_rate=0,
        execution_mode="llm",
        random_visitor_count=4,
        max_concurrency=max_concurrency,
        creator_persona_path=ROOT / "configs/reference/creators.yaml",
        viewer_persona_path=ROOT / "configs/reference/viewers.yaml",
        llm_runtime_path=ROOT / "configs/reference/models.yaml",
        creator_ids=[
            "creator_001",
            "creator_002",
            "creator_003",
            "creator_004",
        ],
        creator_model_aliases=["fake_usd", "fake_cny"],
        viewer_model_aliases=["fake_usd", "fake_cny"],
        output_root=tmp_path,
    )
