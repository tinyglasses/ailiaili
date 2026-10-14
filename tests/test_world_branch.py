from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
import yaml
from sqlalchemy import delete, func, select

from ailiaili.config import SimulationConfig
from ailiaili.persistence.models import (
    CreatorRow,
    CreatorTurnRow,
    DailyMetricRow,
    HomepageSessionRow,
    PanelShownHistoryRow,
    RunRow,
    VideoRow,
    ViewerClickHistoryRow,
)
from ailiaili.simulation.runner import SimulationRunner
from ailiaili.world_branch import create_world_snapshot, fork_world
import ailiaili.world_branch as world_branch


def source_config(tmp_path: Path) -> SimulationConfig:
    return SimulationConfig(
        run_id="branch-source",
        seed=1234,
        world_anchor_date="2026-08-13",
        days=3,
        creator_count=4,
        viewer_count=8,
        homepage_size=4,
        max_clicks=3,
        publish_interval_days=1,
        initial_videos_per_creator=2,
        cold_impression_target=3,
        exploit_ratio=0.5,
        cold_ratio=0.25,
        explore_ratio=0.25,
        max_videos_per_creator=2,
        output_root=tmp_path,
    )


def test_snapshot_and_fork_preserve_world_state_without_parent_archive(
    tmp_path: Path,
) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(2)
    snapshot_path = tmp_path / "snapshots" / "day-002.ailiaili-world.zip"

    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=snapshot_path,
        simulation_day=2,
    )

    assert snapshot.simulation_day == 2
    source_database_bytes = sum(
        item.stat().st_size
        for item in source.config.run_dir.glob("ailiaili.db*")
    )
    assert snapshot.archive_bytes < source_database_bytes
    with zipfile.ZipFile(snapshot.path) as archive:
        metadata = json.loads(archive.read("snapshot.json"))
    assert metadata["format"] == "ailiaili-world-snapshot-v1"
    assert "historical llm_calls and llm_attempts" in metadata["omitted"]

    with pytest.raises(ValueError):
        fork_world(
            snapshot_path=snapshot.path,
            run_id="../escape",
            additional_days=1,
            purpose="invalid path must never be materialized",
            output_root=tmp_path / "branches",
        )

    branch = fork_world(
        snapshot_path=snapshot.path,
        run_id="branch-experiment",
        additional_days=1,
        purpose="test one transparent rule change",
        output_root=tmp_path / "branches",
        config_overrides={"exploit_ratio": 0.25, "explore_ratio": 0.5},
    )
    config = SimulationConfig.from_yaml(branch.config_path)
    assert config.parent_run_id == source.config.run_id
    assert config.parent_day == 2
    assert config.days == 3
    assert config.canonical is False
    assert config.evidence_mode == "compact"
    assert config.exploit_ratio == 0.25
    assert config.explore_ratio == 0.5
    # Stub worlds have no external actor/runtime dependencies.
    assert config.dependency_sha256 == {}
    assert config.continuation_code_sha256 is not None

    runner = SimulationRunner(config)
    with runner.database.session() as session:
        run = session.get(RunRow, config.run_id)
        assert run is not None and run.last_completed_day == 2
        assert session.scalar(
            select(func.count()).select_from(CreatorRow)
        ) == source.config.creator_count
        assert session.scalar(
            select(func.count()).select_from(VideoRow)
        ) > 0
        assert session.scalar(
            select(func.count()).select_from(DailyMetricRow)
        ) > 0
        # The compact tables are the continuation truth; representative
        # homepage rows exist only for playback/reporting.
        assert session.scalar(
            select(func.count()).select_from(ViewerClickHistoryRow)
        ) > 0
        representative_sessions = session.scalar(
            select(func.count()).select_from(HomepageSessionRow)
        )
        assert 0 < representative_sessions < 16

    summaries = runner.run_through(3)
    assert [item.simulation_day for item in summaries] == [3]
    assert not (config.run_dir / "site").exists()
    with runner.database.session() as session:
        run = session.get(RunRow, config.run_id)
        assert run is not None and run.last_completed_day == 3


def test_branch_uses_compacted_click_history_as_exclusions(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(2)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "source.zip",
    )
    branch = fork_world(
        snapshot_path=snapshot.path,
        run_id="history-branch",
        additional_days=1,
        purpose="verify inherited viewer state",
        output_root=tmp_path / "branches",
    )
    runner = SimulationRunner(SimulationConfig.from_yaml(branch.config_path))
    with runner.database.session() as session:
        compact_rows = session.scalars(select(ViewerClickHistoryRow)).all()
        expected: dict[str, set[str]] = {}
        for row in compact_rows:
            expected.setdefault(row.viewer_id, set()).add(row.video_id)
        actual = runner._historical_clicks(session, before_day=3)
    assert expected
    assert all(values <= actual[viewer_id] for viewer_id, values in expected.items())


def test_older_day_snapshot_does_not_leak_later_creator_memory(
    tmp_path: Path,
) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(2)
    with source.database.session() as session:
        creator_id = str(
            session.scalar(
                select(CreatorRow.creator_id).order_by(CreatorRow.creator_id)
            )
        )
    future_memory = {
        "current_direction": "THIS MEMORY BELONGS TO THE FUTURE",
        "beliefs": ["future"],
        "memorable_experiences": [],
        "ideas_to_try": [],
        "unresolved_tension": None,
    }
    with source.database.session() as session, session.begin():
        creator = session.get(CreatorRow, creator_id)
        assert creator is not None
        creator.memory_json = future_memory
        creator.persona_json = {
            "创世时的直觉": ["先相信一个真正属于自己的选题。"],
            "你长期带着的矛盾": "想被看见，但不想被流量同化。",
        }
        creator.declaration_json = {
            "我最初认为什么内容会成功": "先相信一个真正属于自己的选题。"
        }
        # Model a rare Day-0/Day-1 technical failure followed by a later
        # success whose current-memory row must not travel back to Day 1.
        session.execute(
            delete(CreatorTurnRow).where(
                CreatorTurnRow.creator_id == creator_id,
                CreatorTurnRow.simulation_day <= 1,
            )
        )

    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "older-day.zip",
        simulation_day=1,
    )
    branch = fork_world(
        snapshot_path=snapshot.path,
        run_id="older-day-memory-branch",
        additional_days=1,
        purpose="verify memory rollback",
        output_root=tmp_path / "branches",
    )
    runner = SimulationRunner(SimulationConfig.from_yaml(branch.config_path))
    with runner.database.session() as session:
        creator = session.get(CreatorRow, creator_id)
        assert creator is not None
        restored_memory = dict(creator.memory_json)
    assert restored_memory["current_direction"] == (
        "先按自己的创作直觉做第一期，不假装已经知道平台规律。"
    )
    assert restored_memory["beliefs"] == ["先相信一个真正属于自己的选题。"]
    assert restored_memory["memorable_experiences"] == []
    assert restored_memory != future_memory


def test_world_lineage_configuration_rejects_partial_parent() -> None:
    base = {
        "run_id": "invalid-lineage",
        "seed": 1,
        "world_anchor_date": "2026-08-13",
    }
    try:
        SimulationConfig.model_validate({**base, "parent_run_id": "parent"})
    except ValueError as error:
        assert "parent_run_id and parent_day" in str(error)
    else:  # pragma: no cover
        raise AssertionError("partial lineage must be rejected")


def test_fork_rejects_population_override(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "protected.zip",
    )
    with pytest.raises(ValueError, match="unsupported branch override"):
        fork_world(
            snapshot_path=snapshot.path,
            run_id="invalid-population-branch",
            additional_days=1,
            purpose="must not mutate inherited actor population",
            output_root=tmp_path / "branches",
            config_overrides={"viewer_count": 999},
        )


def test_fork_rejects_seed_override(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "seed-protected.zip",
    )
    with pytest.raises(ValueError, match="unsupported branch override"):
        fork_world(
            snapshot_path=snapshot.path,
            run_id="invalid-seed-branch",
            additional_days=1,
            purpose="seed changes actor generation and must stay frozen",
            output_root=tmp_path / "branches",
            config_overrides={"seed": 987654},
        )


def test_compact_branch_cannot_fork_before_its_inherited_parent_day(
    tmp_path: Path,
) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(2)
    first_snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "parent.zip",
        simulation_day=1,
    )
    branch = fork_world(
        snapshot_path=first_snapshot.path,
        run_id="nested-source-branch",
        additional_days=2,
        purpose="create a branch with more than one new completed day",
        output_root=tmp_path / "branches",
    )
    runner = SimulationRunner(SimulationConfig.from_yaml(branch.config_path))
    runner.run_through(3)

    with pytest.raises(ValueError, match="before its inherited parent day"):
        create_world_snapshot(
            source_run_dir=branch.run_dir,
            output_path=tmp_path / "illegal-rollback.zip",
            simulation_day=0,
        )
    later_completed_day = create_world_snapshot(
        source_run_dir=branch.run_dir,
        output_path=tmp_path / "legal-later-day.zip",
        simulation_day=2,
    )
    assert later_completed_day.simulation_day == 2


def test_nested_fork_rejects_source_branch_code_drift(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    parent_snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "canonical-parent.zip",
        simulation_day=1,
    )
    parent_branch = fork_world(
        snapshot_path=parent_snapshot.path,
        run_id="old-code-parent-branch",
        additional_days=1,
        purpose="model a branch tied to an older continuation contract",
        output_root=tmp_path / "branches",
    )
    with sqlite3.connect(parent_branch.database_path) as connection:
        row = connection.execute(
            "SELECT config_json FROM runs WHERE run_id=?",
            (parent_branch.run_id,),
        ).fetchone()
        assert row is not None
        stored = json.loads(row[0])
        stored["continuation_code_sha256"] = "0" * 64
        connection.execute(
            "UPDATE runs SET config_json=? WHERE run_id=?",
            (
                json.dumps(stored, ensure_ascii=False),
                parent_branch.run_id,
            ),
        )
        connection.commit()
    # Model a genuinely old branch, whose launch file and DB agree about the
    # old code contract; otherwise snapshot creation now rejects YAML drift.
    old_launch = yaml.safe_load(parent_branch.config_path.read_text(encoding="utf-8"))
    old_launch["continuation_code_sha256"] = "0" * 64
    parent_branch.config_path.write_text(yaml.safe_dump(old_launch), encoding="utf-8")
    stale_snapshot = create_world_snapshot(
        source_run_dir=parent_branch.run_dir,
        output_path=tmp_path / "stale-branch.zip",
        simulation_day=1,
    )

    with pytest.raises(ValueError, match="continuation code has drifted"):
        fork_world(
            snapshot_path=stale_snapshot.path,
            run_id="must-not-cross-code-contracts",
            additional_days=1,
            purpose="old branch history must not silently acquire new rules",
            output_root=tmp_path / "nested",
        )


def test_snapshot_rejects_invalidated_source(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    with source.database.session() as session, session.begin():
        run = session.get(RunRow, source.config.run_id)
        assert run is not None
        run.status = "invalidated"
    with pytest.raises(ValueError, match="invalidated"):
        create_world_snapshot(
            source_run_dir=source.config.run_dir,
            output_path=tmp_path / "must-not-exist.zip",
        )


def test_fork_rejects_metadata_lineage_that_disagrees_with_database(
    tmp_path: Path,
) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(2)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "original.zip",
        simulation_day=2,
    )
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(snapshot.path, "r") as original, zipfile.ZipFile(
        tampered,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as rewritten:
        metadata = json.loads(original.read("snapshot.json"))
        metadata["source_day"] = 1
        rewritten.writestr(
            "snapshot.json",
            json.dumps(metadata, ensure_ascii=False),
        )
        rewritten.writestr("world.db", original.read("world.db"))

    with pytest.raises(ValueError, match="lineage does not match"):
        fork_world(
            snapshot_path=tampered,
            run_id="must-not-fork",
            additional_days=1,
            purpose="metadata cannot pretend this is an earlier world",
            output_root=tmp_path / "branches",
        )


def test_branch_dependency_hash_rejects_drift(tmp_path: Path) -> None:
    dependency = tmp_path / "personas.yaml"
    dependency.write_text("version: one\n", encoding="utf-8")
    digest = __import__("hashlib").sha256(dependency.read_bytes()).hexdigest()
    config = source_config(tmp_path).model_copy(
        update={
            "run_id": "dependency-branch",
            "days": 3,
            "parent_run_id": "some-parent",
            "parent_day": 2,
            "canonical": False,
            "creator_persona_path": dependency,
            "dependency_sha256": {"creator_persona_path": digest},
        }
    )
    runner = SimulationRunner(config)
    dependency.write_text("version: two\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="dependency changed"):
        runner.initialize()
    assert not config.database_path.exists()


def test_branch_continuation_code_hash_rejects_drift(tmp_path: Path) -> None:
    config = source_config(tmp_path).model_copy(
        update={
            "run_id": "code-drift-branch",
            "days": 3,
            "parent_run_id": "some-parent",
            "parent_day": 2,
            "canonical": False,
            "continuation_code_sha256": "0" * 64,
        }
    )
    runner = SimulationRunner(config)
    with pytest.raises(RuntimeError, match="continuation code changed"):
        runner.initialize()
    assert not config.database_path.exists()


def test_llm_branch_cannot_be_initialized_without_services(tmp_path: Path) -> None:
    config = source_config(tmp_path).model_copy(
        update={
            "run_id": "llm-service-guard",
            "execution_mode": "llm",
            "creator_persona_path": tmp_path / "creators.yaml",
            "viewer_persona_path": tmp_path / "viewers.yaml",
            "llm_runtime_path": tmp_path / "runtime.yaml",
            "creator_model_alias": "creator-model",
            "viewer_model_alias": "viewer-model",
            "initial_videos_per_creator": 1,
        }
    )
    runner = SimulationRunner(config)
    with pytest.raises(RuntimeError, match=r"with_llm"):
        runner.initialize()
    assert not config.database_path.exists()


def test_fork_preserves_explicit_tail_policy(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir, output_path=tmp_path / "tail.zip"
    )
    policy = {
        "viewer_tail_gate_seconds": 600,
        "viewer_tail_recheck_seconds": 60,
        "viewer_tail_completion_ratio": 0.99,
    }
    branch = fork_world(
        snapshot_path=snapshot.path, run_id="tail-branch", additional_days=1,
        purpose="explicit operational settings must reach the runner",
        output_root=tmp_path / "branches", config_overrides=policy,
    )
    restored = SimulationConfig.from_yaml(branch.config_path)
    assert {key: getattr(restored, key) for key in policy} == policy
    child_snapshot = create_world_snapshot(
        source_run_dir=branch.run_dir, output_path=tmp_path / "nested-tail.zip"
    )
    child = fork_world(
        snapshot_path=child_snapshot.path, run_id="tail-child", additional_days=1,
        purpose="retain operational settings through another snapshot",
        output_root=tmp_path / "branches",
    )
    child_config = SimulationConfig.from_yaml(child.config_path)
    assert {key: getattr(child_config, key) for key in policy} == policy


def test_snapshot_preserves_failed_panel_exposure_history(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    with source.database.session() as session, session.begin():
        row = session.scalars(select(HomepageSessionRow)).first()
        assert row is not None
        row.mirror_group_id = "panel-with-failed-call"
        row.status = "skipped"
    with source.database.session() as session:
        expected = source._panel_shown_history(session, before_day=2)
    assert expected["panel-with-failed-call"]
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir, output_path=tmp_path / "panel.zip"
    )
    branch = fork_world(
        snapshot_path=snapshot.path, run_id="panel-branch", additional_days=1,
        purpose="compaction must match existing runner exclusion semantics",
        output_root=tmp_path / "branches",
    )
    runner = SimulationRunner(SimulationConfig.from_yaml(branch.config_path))
    with runner.database.session() as session:
        assert runner._panel_shown_history(session, before_day=2) == expected


def test_snapshot_survives_external_dependency_removal(tmp_path: Path) -> None:
    dependency = tmp_path / "original-personas.yaml"
    dependency.write_text("version: original\n", encoding="utf-8")
    config = source_config(tmp_path).model_copy(
        update={"creator_persona_path": dependency}
    )
    source = SimulationRunner(config)
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=config.run_dir, output_path=tmp_path / "portable.zip"
    )
    dependency.unlink()
    branch = fork_world(
        snapshot_path=snapshot.path, run_id="portable-branch", additional_days=1,
        purpose="configuration belongs to the snapshot, not a mutable path",
        output_root=tmp_path / "branches",
    )
    restored = SimulationConfig.from_yaml(branch.config_path)
    assert restored.creator_persona_path.is_relative_to(branch.run_dir)
    assert restored.creator_persona_path.read_text(encoding="utf-8") == "version: original\n"
    SimulationRunner(restored).run_through(2)


def test_snapshot_handles_reserved_uri_characters(tmp_path: Path) -> None:
    config = source_config(tmp_path / "world #1 %20")
    source = SimulationRunner(config)
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=config.run_dir, output_path=tmp_path / "uri.zip"
    )
    assert snapshot.simulation_day == 1


def test_no_change_fork_matches_uninterrupted_stub_world(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(3)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir,
        output_path=tmp_path / "past-day.zip", simulation_day=1,
    )
    branch = fork_world(
        snapshot_path=snapshot.path, run_id="no-change", additional_days=2,
        purpose="state-preserving continuation, not LLM reproducibility",
        output_root=tmp_path / "branches",
    )
    SimulationRunner(SimulationConfig.from_yaml(branch.config_path)).run_through(3)
    queries = [
        "SELECT video_id,title,premise,outline_json FROM videos ORDER BY video_id",
        "SELECT creator_id,memory_json FROM creators ORDER BY creator_id",
        "SELECT simulation_day,entity_type,entity_id,impressions,clicks,new_follows,triples "
        "FROM daily_metrics ORDER BY simulation_day,entity_type,entity_id",
        "SELECT viewer_id,creator_id,created_day FROM follows ORDER BY viewer_id,creator_id",
        "SELECT homepage_session_id,candidate_items_json,clicked_video_ids_json "
        "FROM homepage_sessions WHERE simulation_day>1 ORDER BY homepage_session_id",
    ]
    with sqlite3.connect(source.config.database_path) as parent, sqlite3.connect(branch.database_path) as child:
        for query in queries:
            assert parent.execute(query).fetchall() == child.execute(query).fetchall(), query


def test_failed_snapshot_publish_leaves_no_final_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    output = tmp_path / "unfinished.zip"

    def fail_write(*args, **kwargs):
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(zipfile.ZipFile, "writestr", fail_write)
    with pytest.raises(OSError, match="simulated disk"):
        create_world_snapshot(source_run_dir=source.config.run_dir, output_path=output)
    assert not output.exists()
    assert not list(tmp_path.glob("ailiaili-snapshot-*"))


def test_snapshot_publish_does_not_overwrite_racing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    output = tmp_path / "claimed.zip"
    real_link = world_branch.os.link

    def race_link(source_path, target_path):
        Path(target_path).write_bytes(b"another snapshot owns this path")
        return real_link(source_path, target_path)

    monkeypatch.setattr(world_branch.os, "link", race_link)
    with pytest.raises(FileExistsError):
        create_world_snapshot(source_run_dir=source.config.run_dir, output_path=output)
    assert output.read_bytes() == b"another snapshot owns this path"


def test_bundled_dependency_tampering_is_rejected(tmp_path: Path) -> None:
    dependency = tmp_path / "personas.yaml"
    dependency.write_text("version: original\n", encoding="utf-8")
    source = SimulationRunner(source_config(tmp_path).model_copy(
        update={"creator_persona_path": dependency}
    ))
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir, output_path=tmp_path / "good.zip"
    )
    tampered = tmp_path / "changed.zip"
    with zipfile.ZipFile(snapshot.path) as original, zipfile.ZipFile(tampered, "w") as archive:
        for name in original.namelist():
            archive.writestr(name, b"version: changed\n" if name.startswith("dependencies/") else original.read(name))
    with pytest.raises(ValueError, match="dependency checksum"):
        fork_world(
            snapshot_path=tampered, run_id="bad-dependency", additional_days=1,
            purpose="reject corrupted dependency", output_root=tmp_path / "branches",
        )
    assert not (tmp_path / "branches" / "bad-dependency").exists()


def test_legacy_snapshot_explicitly_warns_about_unverified_dependencies(tmp_path: Path) -> None:
    dependency = tmp_path / "personas.yaml"
    dependency.write_text("version: current\n", encoding="utf-8")
    source = SimulationRunner(source_config(tmp_path).model_copy(
        update={"creator_persona_path": dependency}
    ))
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir, output_path=tmp_path / "new.zip"
    )
    legacy = tmp_path / "legacy.zip"
    with zipfile.ZipFile(snapshot.path) as original, zipfile.ZipFile(legacy, "w") as archive:
        metadata = json.loads(original.read("snapshot.json"))
        metadata.pop("dependencies")
        archive.writestr("snapshot.json", json.dumps(metadata))
        archive.writestr("world.db", original.read("world.db"))
    with pytest.warns(RuntimeWarning, match="Legacy snapshot"):
        branch = fork_world(
            snapshot_path=legacy, run_id="legacy-branch", additional_days=1,
            purpose="legacy input remains readable but cannot prove old files",
            output_root=tmp_path / "branches",
        )
    config = SimulationConfig.from_yaml(branch.config_path)
    assert config.creator_persona_path.read_bytes() == dependency.read_bytes()


def test_branch_cannot_reuse_parent_identity(tmp_path: Path) -> None:
    source = SimulationRunner(source_config(tmp_path))
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=source.config.run_dir, output_path=tmp_path / "identity.zip"
    )
    with pytest.raises(ValueError, match="differ from its parent"):
        fork_world(
            snapshot_path=snapshot.path, run_id=source.config.run_id, additional_days=1,
            purpose="do not make a self-parent lineage", output_root=tmp_path / "branches",
        )


def test_llm_branch_continues_only_new_day_using_bundled_config(tmp_path: Path) -> None:
    import yaml
    from fake_world import FakeWorldProvider, dual_model_config, dual_model_runtime

    runtime_path = tmp_path / "fake-runtime.yaml"
    runtime_path.write_text(
        yaml.safe_dump(dual_model_runtime().model_dump(mode="json")), encoding="utf-8"
    )
    config = dual_model_config(tmp_path, run_id="llm-parent", max_concurrency=1).model_copy(
        update={"days": 2, "llm_runtime_path": runtime_path}
    )
    source = SimulationRunner.with_llm(config, provider=FakeWorldProvider())
    source.run_through(1)
    snapshot = create_world_snapshot(
        source_run_dir=config.run_dir, output_path=tmp_path / "llm.zip"
    )
    runtime_path.unlink()
    branch = fork_world(
        snapshot_path=snapshot.path, run_id="llm-child", additional_days=1,
        purpose="exercise LLM orchestration with a fake provider, zero network",
        output_root=tmp_path / "branches",
    )
    branch_config = SimulationConfig.from_yaml(branch.config_path)
    provider = FakeWorldProvider()
    runner = SimulationRunner.with_llm(branch_config, provider=provider)
    summaries = runner.run_through(2)
    assert [row.simulation_day for row in summaries] == [2]
    assert provider.calls
    with sqlite3.connect(branch.database_path) as child:
        assert child.execute("SELECT DISTINCT simulation_day FROM llm_calls").fetchall() == [(2,)]
        assert child.execute("SELECT COUNT(*) FROM creators").fetchone()[0] == config.creator_count
