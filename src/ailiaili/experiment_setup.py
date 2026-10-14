"""Small, offline entry points for choosing and understanding an experiment."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import yaml

from ailiaili.simple_config import SimpleWorld, compile_world, yaml_text

TEMPLATES = {
    "stories": "两种说话方式，怎样把日常小事写成故事？",
    "workshop": "同一件生活小事，解释者、动手派和观察者会怎么做？",
    "discovery": "增加探索推荐后，曝光会分散到更多作者吗？",
}


def initialize_world(run_id: str, template: str, output: Path) -> Path:
    if template not in TEMPLATES:
        raise ValueError("Unknown template. Choose: " + ", ".join(TEMPLATES))
    source = Path(__file__).parent / "presets" / f"world-{template}.yaml"
    raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    raw["run_id"] = run_id
    SimpleWorld.model_validate(raw)
    if output.exists():
        raise ValueError("Output already exists; choose another file to preserve your settings")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# 先改角色、分区与问题；mode: stub 不调用模型。\n" + yaml_text(raw), encoding="utf-8", newline="\n")
    return output


def plan_world(source: Path) -> dict:
    world = SimpleWorld.from_yaml(source)
    # The virtual destination is only used to validate generated configuration.
    documents = compile_world(world, Path("configs/local/plan-preview"))
    config = documents["world.yaml"]
    count = len(world.creators)
    scheduled = [sum(i % world.publish_every == (day - 1) % world.publish_every
                     for i in range(count)) for day in range(1, world.days + 1)]
    creator_turns = count + sum(scheduled)
    sessions = world.days * world.viewers.count
    per_session_cap = min(config["homepage_size"], config["max_clicks"])
    logical_min = creator_turns + sessions if world.mode == "llm" else 0
    logical_max = creator_turns + sessions * (1 + per_session_cap) if world.mode == "llm" else 0
    cold = round(config["homepage_size"] * config["cold_ratio"])
    explore = round(config["homepage_size"] * config["explore_ratio"])
    route_counts = Counter(c.model or world.model for c in world.creators)
    routes = []
    if world.mode == "llm":
        for alias in documents["models.yaml"]["models"]:
            model = world.models[alias]
            routes.append({"alias": alias, "model_id": model.model_id,
                           "creators": route_counts[alias],
                           "viewers": world.viewers.count if alias == (world.viewers.model or world.model) else 0,
                           "key_variable": model.api_key_env,
                           "currency": model.currency,
                           "configured_budget_limit": str(world.budget.hard_limit_for(model.currency))})
    return {
        "run_id": world.run_id, "question": world.question, "mode": world.mode,
        "days": world.days, "creators": count, "viewers": world.viewers.count,
        "categories": world.categories,
        "creator_approaches": [{"name": c.name, "approach": c.approach} for c in world.creators],
        "planned_initial_works": count, "planned_daily_new_works": scheduled,
        "planned_works_total": creator_turns, "planned_browse_sessions": sessions,
        "nominal_homepage_slots": {"performance": config["homepage_size"] - cold - explore,
                                   "cold_start": cold, "exploration": explore},
        "model_routes": routes,
        "logical_model_calls_for_full_schedule": {"min": logical_min, "max": logical_max},
        "attempts_upper_bound_with_configured_retries": logical_max * world.budget.max_attempts_per_call,
        "report_directory": str(world.output_root / world.run_id / "site"),
        "notes": [
            "次数按全程完成、每份投稿和首页各一次逻辑调用、点开的作品逐份判断计算；预算/失败会让实际完成更少，重试会增加请求。",
            "这是工作量范围，不是费用报价；实际费用取决于输入输出长度和供应商，显式恢复重试不含在本次上界中。",
            "推荐卡位是四舍五入后的名义分配，候选不足、作者上限和补位会改变实际首页。",
            "stub只跑固定规则，不能据此判断模型能力或角色性格效果。" if world.mode == "stub" else
            "只检查配置，不联网测试模型，不读取密钥值，不启动实验。",
        ],
    }


def render_plan(plan: dict) -> str:
    approaches = {"storyteller": "讲故事", "explainer": "解释原理", "maker": "设计与尝试", "observer": "观察生活"}
    lines = [f"{plan['run_id']} · {plan['mode']} · {plan['days']}天",
             f"{plan['creators']}个创作者 / {plan['viewers']}个观众；分区：{'、'.join(plan['categories'])}"]
    if plan['question']:
        lines.append("观察问题：" + plan['question'])
    lines += [f"计划投稿：初始{plan['planned_initial_works']}份，总计{plan['planned_works_total']}份；计划浏览{plan['planned_browse_sessions']}次。",
              "创作方式：" + "；".join(f"{c['name']}：{approaches[c['approach']]}" for c in plan['creator_approaches'])]
    slots = plan['nominal_homepage_slots']
    lines.append(f"首页名义卡位：表现推荐{slots['performance']} / 冷启动{slots['cold_start']} / 探索{slots['exploration']}")
    calls = plan['logical_model_calls_for_full_schedule']
    lines.append(f"完整计划的逻辑模型调用：{calls['min']}—{calls['max']}；含配置内重试最多{plan['attempts_upper_bound_with_configured_retries']}次请求。")
    for route in plan['model_routes']:
        key = route['key_variable'] or "无鉴权"
        lines.append(f"模型 {route['alias']} ({route['model_id']})：{route['creators']}作者/{route['viewers']}观众；变量 {key}；本run该币种预算 {route['configured_budget_limit']} {route['currency']}")
    lines += ["结果目录：" + plan['report_directory'], *plan['notes']]
    return "\n".join(lines)
