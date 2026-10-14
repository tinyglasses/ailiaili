from __future__ import annotations

from pathlib import Path

from ailiaili.config import SimulationConfig
from ailiaili.simulation.runner import SimulationRunner


def test_locked_latest_status_never_stops_the_world(
    tmp_path: Path, monkeypatch
) -> None:
    runner = SimulationRunner(
        SimulationConfig(
            run_id="runtime-progress",
            seed=7,
            world_anchor_date="2026-08-11",
            creator_count=1,
            viewer_count=1,
            homepage_size=1,
            max_videos_per_creator=1,
            output_root=tmp_path,
        )
    )

    def locked_replace(self: Path, target: Path) -> Path:
        raise PermissionError("temporarily locked by a reader")

    monkeypatch.setattr(Path, "replace", locked_replace)
    runner._emit_progress("test_heartbeat", simulation_day=1, calls=3)

    history = runner.config.run_dir / "runtime_progress.jsonl"
    assert history.exists()
    assert "test_heartbeat" in history.read_text(encoding="utf-8")
