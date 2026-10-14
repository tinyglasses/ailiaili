from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Inspiration(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video_id: str = Field(min_length=1)
    kind: Literal["imitate", "respond", "extend", "invert"]
    note: str = Field(min_length=1, max_length=200)


class VideoDraft(BaseModel):
    model_config = ConfigDict(extra="ignore")

    category: str = Field(min_length=1, max_length=40)
    grounding_mode: Literal[
        "platform_record", "synthesis", "declared_fiction"
    ]
    title: str = Field(min_length=1, max_length=60)
    cover_description: str = Field(min_length=1, max_length=160)
    premise: str = Field(min_length=1, max_length=240)
    outline: list[str] = Field(min_length=3, max_length=5)
    inspiration: Inspiration | None = None

    @model_validator(mode="after")
    def validate_outline(self) -> "VideoDraft":
        if any(not item.strip() for item in self.outline):
            raise ValueError("outline items cannot be blank")
        if sum(len(item) for item in self.outline) > 800:
            raise ValueError("outline total length cannot exceed 800 characters")
        return self


class MemorableExperience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video_id: str = Field(min_length=1)
    takeaway: str = Field(min_length=1, max_length=300)


class CreatorMemory(BaseModel):
    model_config = ConfigDict(extra="ignore")

    current_direction: str = Field(min_length=1, max_length=240)
    beliefs: list[str] = Field(default_factory=list, max_length=3)
    memorable_experiences: list[MemorableExperience] = Field(
        default_factory=list, max_length=3
    )
    ideas_to_try: list[str] = Field(default_factory=list, max_length=2)
    unresolved_tension: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_item_lengths(self) -> "CreatorMemory":
        if any(len(item) > 240 for item in self.beliefs):
            raise ValueError("beliefs cannot exceed 240 characters")
        if any(len(item) > 240 for item in self.ideas_to_try):
            raise ValueError("ideas_to_try cannot exceed 240 characters")
        return self


class CreatorTurnResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    creator_note: str = Field(min_length=1, max_length=500)
    expected_reception: Literal["low", "ordinary", "strong", "breakout"]
    prediction_reason: str = Field(min_length=1, max_length=120)
    updated_memory: CreatorMemory
    video: VideoDraft


class CandidateItem(BaseModel):
    video_id: str
    bucket: Literal["exploit", "cold", "explore"]
    source: Literal["global", "interest", "following"] = "global"


class HomepageDecision(BaseModel):
    """The only model-owned behavior returned from one homepage."""

    model_config = ConfigDict(extra="ignore")

    clicked_video_ids: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_clicked_video_ids(self) -> "HomepageDecision":
        if any(not video_id.strip() for video_id in self.clicked_video_ids):
            raise ValueError("clicked video IDs cannot be blank")
        if len(self.clicked_video_ids) != len(set(self.clicked_video_ids)):
            raise ValueError("clicked video IDs cannot contain duplicates")
        return self


class PostClickDecision(BaseModel):
    model_config = ConfigDict(extra="ignore")

    video_id: str
    follow_available: bool
    follow_intent: bool
    new_follow: bool
    triple: bool
    channel_pull: Literal["none", "open_to_more", "actively_seek"] | None = None
    episode_impact: Literal["ordinary", "notable", "exceptional"] | None = None
    follow_activation_probability: float | None = Field(
        default=None, ge=0, le=1
    )
    triple_activation_probability: float | None = Field(
        default=None, ge=0, le=1
    )
    click_note: str | None = Field(default=None, max_length=80)
    post_click_note: str | None = Field(default=None, max_length=80)


class ViewerPostClickChoice(BaseModel):
    """What the model chooses; platform-owned eligibility is added afterwards."""

    model_config = ConfigDict(extra="ignore")

    video_id: str = Field(min_length=1)
    follow_intent: bool
    triple: bool
    click_note: str | None = Field(default=None, min_length=1, max_length=80)
    post_click_note: str | None = Field(default=None, min_length=1, max_length=80)


class ViewerPostClickActionChoice(BaseModel):
    """Sparse action-list contract used to avoid mandatory-boolean bias."""

    model_config = ConfigDict(extra="ignore")

    video_id: str = Field(min_length=1)
    actions: list[Literal["follow", "triple"]] = Field(
        default_factory=list, max_length=2
    )
    click_note: str | None = Field(default=None, min_length=1, max_length=80)
    post_click_note: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_actions(self) -> "ViewerPostClickActionChoice":
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("post-click actions cannot contain duplicates")
        return self

    @property
    def follow_intent(self) -> bool:
        return "follow" in self.actions

    @property
    def triple(self) -> bool:
        return "triple" in self.actions


class ViewerPostClickAssessment(BaseModel):
    """Model-owned reaction before platform-owned action friction.

    The labels intentionally describe qualitative states instead of scores or
    button presses. This keeps the LLM responsible for taste while letting the
    simulation model the gap between liking and performing a rare action.
    """

    model_config = ConfigDict(extra="ignore")

    video_id: str = Field(min_length=1)
    channel_pull: Literal["none", "open_to_more", "actively_seek"]
    episode_impact: Literal["ordinary", "notable", "exceptional"]
    click_note: str | None = Field(default=None, min_length=1, max_length=80)
    post_click_note: str | None = Field(default=None, min_length=1, max_length=80)


class PostClickDecisionBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decisions: list[ViewerPostClickChoice]


class VideoStats(BaseModel):
    impressions: int = 0
    clicks: int = 0
    completed_follow_opportunities: int = 0
    new_follows: int = 0
    completed_post_click_opportunities: int = 0
    triples: int = 0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def follow_rate(self) -> float:
        denominator = self.completed_follow_opportunities
        return self.new_follows / denominator if denominator else 0.0

    @property
    def triple_rate(self) -> float:
        denominator = self.completed_post_click_opportunities
        return self.triples / denominator if denominator else 0.0


class DaySummary(BaseModel):
    simulation_day: int
    status: Literal["running", "completed"]
    planned_creator_turns: int
    succeeded_creator_turns: int
    skipped_creator_turns: int
    planned_viewer_sessions: int
    succeeded_viewer_sessions: int
    skipped_viewer_sessions: int
    impressions: int
    clicks: int
    new_follows: int
    triples: int


class ObservedVideo(BaseModel):
    video_id: str
    creator_id: str
    creator_name: str
    category: str
    published_day: int
    title: str
    cover_description: str
    premise: str
    outline: list[str]
    impressions: int
    clicks: int
    new_follows: int
    triples: int
    ctr: float
    follow_rate: float
    triple_rate: float


class PublicObservedVideo(BaseModel):
    """What another creator can see without access to the video's backend."""

    video_id: str
    creator_id: str
    creator_name: str
    category: str
    published_day: int
    title: str
    cover_description: str
    premise: str
    outline: list[str]
    clicks: int
    triples: int


class CreatorAccountOverview(BaseModel):
    video_count: int
    cumulative_follows: int
    impressions: int
    clicks: int
    new_follows: int
    triples: int
    ctr: float
    follow_rate: float
    triple_rate: float


class CreatorObservationPack(BaseModel):
    simulation_day: int
    world_anchor_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    creator_id: str
    creator_name: str
    primary_category: str
    persona: dict
    declaration: dict
    current_memory: CreatorMemory
    account_overview: CreatorAccountOverview
    own_recent_videos: list[ObservedVideo]
    global_trending: list[PublicObservedVideo]
    category_trending: list[PublicObservedVideo]
    recent_new_videos: list[PublicObservedVideo]

    @property
    def visible_video_ids(self) -> set[str]:
        return {
            video.video_id
            for collection in (
                self.own_recent_videos,
                self.global_trending,
                self.category_trending,
                self.recent_new_videos,
            )
            for video in collection
        }
