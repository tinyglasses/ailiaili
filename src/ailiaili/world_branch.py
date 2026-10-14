from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import warnings
import zipfile
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

import yaml

from ailiaili.config import SimulationConfig
from ailiaili.continuation_contract import continuation_code_sha256
from ailiaili.persistence.database import Database


SNAPSHOT_FORMAT = "ailiaili-world-snapshot-v1"
SNAPSHOT_DATABASE_NAME = "world.db"
SNAPSHOT_METADATA_NAME = "snapshot.json"
DEPENDENCY_FIELDS = (
    "creator_persona_path", "viewer_persona_path", "llm_runtime_path",
)
TAIL_POLICY_FIELDS = (
    "viewer_tail_gate_seconds", "viewer_tail_recheck_seconds",
    "viewer_tail_completion_ratio",
)

# Only fields that can be changed without rebuilding inherited Creator/Viewer
# identities are accepted by the generic branch command. More invasive
# experiments deserve a dedicated, auditable transformation instead of an
# apparently harmless YAML edit.
BRANCH_OVERRIDE_FIELDS = {
    "random_visitor_count",
    "cold_impression_target",
    "exploit_ratio",
    "cold_ratio",
    "explore_ratio",
    "age_half_life_days",
    "interest_slots_max",
    "following_slots_max",
    "follow_activation_open_to_more",
    "follow_activation_actively_seek",
    "triple_activation_notable",
    "triple_activation_exceptional",
    "viewer_note_mode",
    "random_visitor_note_sample_rate",
    "max_concurrency",
    "request_start_interval_ms",
    "request_start_batch_size",
    "viewer_early_fraction",
    "viewer_tail_gate_seconds",
    "viewer_tail_recheck_seconds",
    "viewer_tail_completion_ratio",
    "overall_success_rate",
    "cohort_model_success_rate",
    "cohort_model_gate_min_tasks",
}


@dataclass(frozen=True)
class SnapshotResult:
    path: Path
    run_id: str
    simulation_day: int
    database_bytes: int
    archive_bytes: int
    counts: dict[str, int]


@dataclass(frozen=True)
class ForkResult:
    run_dir: Path
    config_path: Path
    database_path: Path
    parent_run_id: str
    parent_day: int
    run_id: str
    target_day: int


def create_world_snapshot(
    *,
    source_run_dir: Path,
    output_path: Path,
    simulation_day: int | None = None,
) -> SnapshotResult:
    """Create a portable continuation state without copying raw LLM evidence.

    The snapshot deliberately retains structured world history (videos,
    metrics, creator turns, follows) while replacing old homepage sessions
    with the two compact histories that can affect future recommendation.
    """

    source_run_dir = source_run_dir.resolve()
    source_database = source_run_dir / "ailiaili.db"
    if not source_database.is_file():
        raise FileNotFoundError(f"source database does not exist: {source_database}")
    output_path = output_path.resolve()
    if output_path.exists():
        raise FileExistsError(f"snapshot already exists: {output_path}")

    with _read_only_connection(source_database) as source:
        runs = source.execute(
            "SELECT run_id, seed, status, config_json, last_completed_day, "
            "created_at, updated_at FROM runs"
        ).fetchall()
        if not runs:
            raise ValueError("source database has no run")
        if len(runs) != 1:
            raise ValueError(
                "source database must contain exactly one run; found "
                f"{len(runs)}"
            )
        run = runs[0]
        source_run_id = str(run["run_id"])
        if str(run["status"]) == "invalidated":
            raise ValueError("cannot snapshot an invalidated source run")
        source_config = SimulationConfig.model_validate(
            json.loads(run["config_json"])
        )
        last_completed_day = int(run["last_completed_day"])
        day = last_completed_day if simulation_day is None else simulation_day
        if day < 0 or day > last_completed_day:
            raise ValueError(
                f"snapshot day must be within 0..{last_completed_day}: {day}"
            )
        completed = source.execute(
            "SELECT status FROM days WHERE run_id=? AND simulation_day=?",
            (source_run_id, day),
        ).fetchone()
        if completed is None or completed["status"] != "completed":
            raise ValueError(f"Day {day} is not completed")
        if (
            source_config.parent_day is not None
            and day < source_config.parent_day
        ):
            raise ValueError(
                "a compact branch cannot be snapshotted before its inherited "
                "parent day because older histories are no longer fully "
                "rollbackable"
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Build next to the destination, then publish a complete archive without
    # overwriting an existing name. A failed build must not leave a final ZIP.
    with tempfile.TemporaryDirectory(
        prefix="ailiaili-snapshot-", dir=output_path.parent
    ) as temporary:
        state_database = Path(temporary) / SNAPSHOT_DATABASE_NAME
        counts = _build_state_database(
            source_database=source_database,
            state_database=state_database,
            source_run_id=source_run_id,
            simulation_day=day,
            expected_last_completed_day=last_completed_day,
        )
        database_sha256 = _sha256_file(state_database)
        dependency_payloads, dependency_source_sha256 = _dependency_payloads(source_config)
        operational_settings = {}
        branch_launch = source_run_dir / "branch.yaml"
        if source_config.parent_day is not None and branch_launch.is_file():
            launch_config = SimulationConfig.from_yaml(branch_launch)
            if launch_config.manifest_dict() != source_config.manifest_dict():
                raise ValueError("branch launch configuration differs from stored world")
            operational_settings = {
                field: getattr(launch_config, field) for field in TAIL_POLICY_FIELDS
                if getattr(launch_config, field) is not None
            }
        metadata = {
            "format": SNAPSHOT_FORMAT,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_run_id": source_run_id,
            "source_day": day,
            "database_file": SNAPSHOT_DATABASE_NAME,
            "database_sha256": database_sha256,
            "counts": counts,
            "operational_settings": operational_settings,
            "dependencies": {
                field: {
                    "file": f"dependencies/{field}.yaml",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for field, payload in dependency_payloads.items()
            },
            "dependency_source_sha256": dependency_source_sha256,
            "omitted": [
                "historical homepage_sessions (compacted into click/panel history)",
                "historical llm_calls and llm_attempts",
                "raw request/response evidence",
                "generated site and analysis projections",
            ],
        }
        temporary_archive = Path(temporary) / "snapshot.zip"
        with zipfile.ZipFile(
            temporary_archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            archive.write(state_database, SNAPSHOT_DATABASE_NAME)
            for field, payload in dependency_payloads.items():
                archive.writestr(f"dependencies/{field}.yaml", payload)
            archive.writestr(
                SNAPSHOT_METADATA_NAME,
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )
        # Same-filesystem hard-link publication is atomic and fails if another
        # caller has already claimed the destination (unlike replace()).
        os.link(temporary_archive, output_path)

        return SnapshotResult(
            path=output_path,
            run_id=source_run_id,
            simulation_day=day,
            database_bytes=state_database.stat().st_size,
            archive_bytes=output_path.stat().st_size,
            counts=counts,
        )


def fork_world(
    *,
    snapshot_path: Path,
    run_id: str,
    additional_days: int,
    purpose: str,
    output_root: Path,
    evidence_mode: str = "compact",
    config_overrides: dict[str, Any] | None = None,
) -> ForkResult:
    """Create an isolated, non-canonical branch from a world snapshot."""

    if additional_days < 1:
        raise ValueError("additional_days must be at least 1")
    if evidence_mode not in {"full", "compact"}:
        raise ValueError("evidence_mode must be 'full' or 'compact'")
    snapshot_path = snapshot_path.resolve()
    if not snapshot_path.is_file():
        raise FileNotFoundError(f"snapshot does not exist: {snapshot_path}")

    resolved_output_root = output_root.resolve()

    with tempfile.TemporaryDirectory(prefix="ailiaili-fork-") as temporary:
        temporary_dir = Path(temporary)
        with zipfile.ZipFile(snapshot_path, "r") as archive:
            names = set(archive.namelist())
            required = {SNAPSHOT_DATABASE_NAME, SNAPSHOT_METADATA_NAME}
            if not required <= names:
                raise ValueError("archive is not an AiliAili world snapshot")
            metadata = json.loads(
                archive.read(SNAPSHOT_METADATA_NAME).decode("utf-8")
            )
            if metadata.get("format") != SNAPSHOT_FORMAT:
                raise ValueError("unsupported world snapshot format")
            archived_dependencies = metadata.get("dependencies")
            dependency_payloads = None
            if archived_dependencies is not None:
                if not isinstance(archived_dependencies, dict) or (
                    set(archived_dependencies) - set(DEPENDENCY_FIELDS)
                ):
                    raise ValueError("invalid snapshot dependency manifest")
                dependency_payloads = {}
                for field, entry in archived_dependencies.items():
                    member = f"dependencies/{field}.yaml"
                    if not isinstance(entry, dict) or entry.get("file") != member:
                        raise ValueError("invalid snapshot dependency member")
                    payload = archive.read(member)
                    if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
                        raise ValueError("snapshot dependency checksum does not match metadata")
                    dependency_payloads[field] = payload
            state_database = temporary_dir / SNAPSHOT_DATABASE_NAME
            with archive.open(SNAPSHOT_DATABASE_NAME) as source, state_database.open(
                "wb"
            ) as destination:
                shutil.copyfileobj(source, destination)

        actual_sha256 = _sha256_file(state_database)
        if actual_sha256 != metadata.get("database_sha256"):
            raise ValueError("snapshot database checksum does not match metadata")

        parent_run_id = str(metadata["source_run_id"])
        parent_day = int(metadata["source_day"])
        with _read_only_connection(state_database) as source:
            rows = source.execute(
                "SELECT run_id,status,config_json,last_completed_day FROM runs"
            ).fetchall()
            if len(rows) != 1:
                raise ValueError(
                    "snapshot database must contain exactly one source run"
                )
            row = rows[0]
            database_parent_run_id = str(row["run_id"])
            database_parent_day = int(row["last_completed_day"])
            if (
                database_parent_run_id != parent_run_id
                or database_parent_day != parent_day
            ):
                raise ValueError(
                    "snapshot metadata lineage does not match its database state"
                )
            if str(row["status"]) != "snapshot":
                raise ValueError("snapshot database run is not frozen as a snapshot")
            completed = source.execute(
                "SELECT status FROM days WHERE run_id=? AND simulation_day=?",
                (parent_run_id, parent_day),
            ).fetchone()
            if completed is None or str(completed["status"]) != "completed":
                raise ValueError("snapshot branch point is not a completed day")
            stored_config = json.loads(row["config_json"])

        # Revalidate the merged payload so a CLI-supplied run ID cannot escape
        # output_root and all lineage invariants are enforced before writing.
        parent_config = SimulationConfig.model_validate(stored_config)
        if run_id == parent_run_id:
            raise ValueError("branch run_id must differ from its parent run_id")
        if dependency_payloads is None:
            dependency_payloads, dependency_source_sha256 = _dependency_payloads(parent_config)
            if dependency_payloads:
                warnings.warn(
                    "Legacy snapshot has no bundled configuration. Using current "
                    "dependency files; their original canonical-run versions "
                    "cannot be verified by this snapshot.",
                    RuntimeWarning, stacklevel=2,
                )
        else:
            dependency_source_sha256 = metadata.get("dependency_source_sha256", {
                field: hashlib.sha256(payload).hexdigest()
                for field, payload in dependency_payloads.items()
            })
        required_dependencies = {
            field for field in DEPENDENCY_FIELDS
            if getattr(parent_config, field) is not None
        }
        if set(dependency_payloads) != required_dependencies:
            raise ValueError("snapshot dependencies do not match its configuration")
        current_dependency_sha256 = {
            field: hashlib.sha256(payload).hexdigest()
            for field, payload in dependency_payloads.items()
        }
        if (
            parent_config.dependency_sha256
            and dependency_source_sha256 != parent_config.dependency_sha256
        ):
            raise ValueError(
                "cannot fork from a branch whose external dependencies have drifted"
            )
        current_code_sha256 = continuation_code_sha256()
        if (
            parent_config.continuation_code_sha256 is not None
            and parent_config.continuation_code_sha256 != current_code_sha256
        ):
            raise ValueError(
                "cannot fork from a branch whose continuation code has drifted"
            )
        branch_payload = parent_config.model_dump(mode="python")
        operational_settings = metadata.get("operational_settings", {})
        if not isinstance(operational_settings, dict) or (
            set(operational_settings) - set(TAIL_POLICY_FIELDS)
        ):
            raise ValueError("invalid snapshot operational settings")
        branch_payload.update(operational_settings)
        branch_payload.update(
            {
                "run_id": run_id,
                "days": parent_day + additional_days,
                "output_root": resolved_output_root,
                "parent_run_id": parent_run_id,
                "parent_day": parent_day,
                "experiment_purpose": purpose,
                "canonical": False,
                "evidence_mode": evidence_mode,
                "dependency_sha256": current_dependency_sha256,
                "continuation_code_sha256": current_code_sha256,
            }
        )
        if config_overrides:
            forbidden = sorted(set(config_overrides) - BRANCH_OVERRIDE_FIELDS)
            if forbidden:
                raise ValueError(
                    "unsupported branch override fields: "
                    + ", ".join(forbidden)
                    + "; allowed fields: "
                    + ", ".join(sorted(BRANCH_OVERRIDE_FIELDS))
                )
            branch_payload.update(config_overrides)
        branch_config = SimulationConfig.model_validate(branch_payload)
        run_dir = resolved_output_root / branch_config.run_id
        if run_dir.exists():
            raise FileExistsError(
                f"branch run directory already exists: {run_dir}"
            )
        branch_config = branch_config.model_copy(update={
            field: run_dir / "dependencies" / f"{field}.yaml"
            for field in dependency_payloads
        })

        run_dir.mkdir(parents=True, exist_ok=False)
        database_path = run_dir / "ailiaili.db"
        try:
            for field, payload in dependency_payloads.items():
                dependency_path = getattr(branch_config, field)
                dependency_path.parent.mkdir(exist_ok=True)
                dependency_path.write_bytes(payload)
            _materialize_branch_database(
                state_database=state_database,
                database_path=database_path,
                parent_run_id=parent_run_id,
                branch_config=branch_config,
            )
            config_path = run_dir / "branch.yaml"
            launch_payload = branch_config.model_dump(mode="json")
            # These operational fields deliberately do not enter the world
            # manifest, but must survive serialization of the launch YAML.
            launch_payload.update({
                field: getattr(branch_config, field) for field in TAIL_POLICY_FIELDS
                if getattr(branch_config, field) is not None
            })
            config_path.write_text(
                yaml.safe_dump(
                    launch_payload,
                    allow_unicode=True,
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (run_dir / "manifest.json").write_text(
                json.dumps(
                    branch_config.manifest_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (run_dir / "branch.json").write_text(
                json.dumps(
                    {
                        "kind": "world_branch",
                        "canonical": False,
                        "run_id": run_id,
                        "parent_run_id": parent_run_id,
                        "parent_day": parent_day,
                        "target_day": parent_day + additional_days,
                        "purpose": purpose,
                        "snapshot_file": snapshot_path.name,
                        "snapshot_database_sha256": actual_sha256,
                        "evidence_mode": evidence_mode,
                        "changes": {
                            field: {"before": getattr(parent_config, field),
                                    "after": getattr(branch_config, field)}
                            for field in (config_overrides or {})
                        },
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(run_dir, ignore_errors=True)
            raise

    return ForkResult(
        run_dir=run_dir,
        config_path=config_path,
        database_path=database_path,
        parent_run_id=parent_run_id,
        parent_day=parent_day,
        run_id=run_id,
        target_day=parent_day + additional_days,
    )


def _build_state_database(
    *,
    source_database: Path,
    state_database: Path,
    source_run_id: str,
    simulation_day: int,
    expected_last_completed_day: int,
) -> dict[str, int]:
    database = Database(state_database)
    database.create_schema()
    database.engine.dispose()

    counts: dict[str, int] = {}
    with _read_only_connection(source_database) as source, closing(
        sqlite3.connect(state_database)
    ) as destination:
        # Keep one coherent read view even when the canonical runner happens
        # to be appending a later day while an older completed day is being
        # snapshotted.  Without this explicit transaction, separate SELECTs
        # could observe different WAL commits.
        source.execute("BEGIN")
        destination.execute("PRAGMA foreign_keys=OFF")
        run = source.execute(
            "SELECT run_id, seed, status, config_json, last_completed_day, "
            "created_at, updated_at FROM runs WHERE run_id=?",
            (source_run_id,),
        ).fetchone()
        if run is None:
            raise ValueError("source run disappeared while building snapshot")
        if str(run["status"]) == "invalidated":
            raise ValueError("cannot snapshot an invalidated source run")
        if int(run["last_completed_day"]) != expected_last_completed_day:
            raise RuntimeError(
                "source world advanced while the snapshot was starting; retry "
                "after the current day finishes or while the run is paused"
            )
        if simulation_day > int(run["last_completed_day"]):
            raise ValueError("source run has not completed the requested day")
        completed = source.execute(
            "SELECT status FROM days WHERE run_id=? AND simulation_day=?",
            (source_run_id, simulation_day),
        ).fetchone()
        if completed is None or str(completed["status"]) != "completed":
            raise ValueError(f"Day {simulation_day} is not completed")
        destination.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                source_run_id,
                run["seed"],
                "snapshot",
                run["config_json"],
                simulation_day,
                run["created_at"],
                run["updated_at"],
            ),
        )

        copies = (
            (
                "days",
                "SELECT * FROM days WHERE run_id=? AND simulation_day<=?",
                (source_run_id, simulation_day),
            ),
            (
                "creators",
                "SELECT * FROM creators WHERE run_id=?",
                (source_run_id,),
            ),
            (
                "viewers",
                "SELECT * FROM viewers WHERE run_id=?",
                (source_run_id,),
            ),
            (
                "videos",
                "SELECT * FROM videos WHERE run_id=? AND published_day<=?",
                (source_run_id, simulation_day),
            ),
            (
                "creator_turns",
                "SELECT * FROM creator_turns WHERE run_id=? AND simulation_day<=?",
                (source_run_id, simulation_day),
            ),
            (
                "follows",
                "SELECT * FROM follows WHERE run_id=? AND created_day<=?",
                (source_run_id, simulation_day),
            ),
            (
                "daily_metrics",
                "SELECT * FROM daily_metrics WHERE run_id=? AND simulation_day<=?",
                (source_run_id, simulation_day),
            ),
        )
        for table, query, parameters in copies:
            counts[table] = _copy_query(
                source=source,
                destination=destination,
                table=table,
                query=query,
                parameters=parameters,
            )

        # CreatorRow stores the current memory.  When branching from an older
        # completed day of a longer archive, roll it back to the last memory
        # that actually existed at that point instead of leaking future state.
        _restore_creator_memories_at_branch_point(
            destination=destination,
            source_run_id=source_run_id,
        )

        counts["homepage_sessions"] = _copy_representative_homepages(
            source=source,
            destination=destination,
            run_id=source_run_id,
            through_day=simulation_day,
        )

        click_history = _compact_click_history(
            source=source,
            run_id=source_run_id,
            through_day=simulation_day,
        )
        destination.executemany(
            "INSERT INTO viewer_click_history "
            "(run_id,viewer_id,video_id,first_clicked_day,last_clicked_day) "
            "VALUES (?,?,?,?,?)",
            (
                (source_run_id, viewer_id, video_id, first_day, last_day)
                for (viewer_id, video_id), (first_day, last_day) in click_history.items()
            ),
        )
        counts["viewer_click_history"] = len(click_history)

        panel_history = _compact_panel_history(
            source=source,
            run_id=source_run_id,
            through_day=simulation_day,
        )
        destination.executemany(
            "INSERT INTO panel_shown_history "
            "(run_id,mirror_group_id,video_id,last_shown_day) VALUES (?,?,?,?)",
            (
                (source_run_id, group_id, video_id, last_day)
                for (group_id, video_id), last_day in panel_history.items()
            ),
        )
        counts["panel_shown_history"] = len(panel_history)
        destination.commit()
        _assert_database_integrity(destination)
        destination.execute("VACUUM")
    return counts


def _materialize_branch_database(
    *,
    state_database: Path,
    database_path: Path,
    parent_run_id: str,
    branch_config: SimulationConfig,
) -> None:
    database = Database(database_path)
    database.create_schema()
    database.engine.dispose()

    branch_run_id = branch_config.run_id
    with _read_only_connection(state_database) as source, closing(
        sqlite3.connect(database_path)
    ) as destination:
        destination.execute("PRAGMA foreign_keys=OFF")
        parent = source.execute(
            "SELECT seed, created_at FROM runs WHERE run_id=?",
            (parent_run_id,),
        ).fetchone()
        if parent is None:
            raise ValueError("snapshot source run is missing")
        now = datetime.now(timezone.utc).isoformat()
        destination.execute(
            "INSERT INTO runs "
            "(run_id,seed,status,config_json,last_completed_day,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                branch_run_id,
                branch_config.seed,
                "running",
                json.dumps(
                    branch_config.manifest_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                branch_config.parent_day,
                now,
                now,
            ),
        )

        for table in (
            "days",
            "creators",
            "viewers",
            "videos",
            "creator_turns",
            "follows",
            "daily_metrics",
            "homepage_sessions",
            "viewer_click_history",
            "panel_shown_history",
        ):
            columns = _table_columns(source, table)
            run_index = columns.index("run_id")
            rows = source.execute(
                f'SELECT {",".join(_quote(item) for item in columns)} '
                f'FROM {_quote(table)} WHERE run_id=?',
                (parent_run_id,),
            )

            def remapped() -> Iterator[tuple[Any, ...]]:
                for row in rows:
                    values = list(row)
                    values[run_index] = branch_run_id
                    yield tuple(values)

            placeholders = ",".join("?" for _ in columns)
            destination.executemany(
                f'INSERT INTO {_quote(table)} '
                f'({",".join(_quote(item) for item in columns)}) '
                f'VALUES ({placeholders})',
                remapped(),
            )

        # These are INTEGER PRIMARY KEY columns rather than SQLite's explicit
        # AUTOINCREMENT form. SQLite will allocate max(existing)+1 naturally;
        # there is intentionally no sqlite_sequence dependency.
        destination.commit()
        _assert_database_integrity(destination)
        destination.execute("VACUUM")


def _restore_creator_memories_at_branch_point(
    *, destination: sqlite3.Connection, source_run_id: str
) -> None:
    """Remove future memory when snapshotting an older completed day.

    Most creators have a successful Day-0 turn, so the last retained turn is
    their exact state.  If Day 0 technically failed and no success existed yet,
    the Creator row in the full archive may contain a later successful memory.
    In that rare case the declaration/persona lets us reconstruct the original
    thin memory instead of leaking that future result backwards.
    """

    latest_memories = {
        str(row[0]): json.loads(row[1]) if isinstance(row[1], str) else row[1]
        for row in destination.execute(
            "SELECT t.creator_id,t.memory_json FROM creator_turns t "
            "JOIN (SELECT creator_id,MAX(simulation_day) AS latest_day "
            "FROM creator_turns WHERE run_id=? AND status='succeeded' "
            "AND memory_json IS NOT NULL GROUP BY creator_id) latest "
            "ON latest.creator_id=t.creator_id "
            "AND latest.latest_day=t.simulation_day "
            "WHERE t.run_id=? AND t.status='succeeded' "
            "AND t.memory_json IS NOT NULL",
            (source_run_id, source_run_id),
        )
    }
    for creator_id, persona_encoded, declaration_encoded in destination.execute(
        "SELECT creator_id,persona_json,declaration_json FROM creators "
        "WHERE run_id=?",
        (source_run_id,),
    ):
        creator_id = str(creator_id)
        memory = latest_memories.get(creator_id)
        if memory is None:
            persona = (
                json.loads(persona_encoded)
                if isinstance(persona_encoded, str)
                else persona_encoded
            )
            declaration = (
                json.loads(declaration_encoded)
                if isinstance(declaration_encoded, str)
                else declaration_encoded
            )
            beliefs = list(persona.get("创世时的直觉", []))
            if not beliefs:
                first_belief = declaration.get("我最初认为什么内容会成功")
                beliefs = [first_belief] if first_belief else []
            memory = {
                "current_direction": (
                    "先按自己的创作直觉做第一期，不假装已经知道平台规律。"
                ),
                "beliefs": beliefs,
                "memorable_experiences": [],
                "ideas_to_try": [],
                "unresolved_tension": persona.get("你长期带着的矛盾"),
            }
        destination.execute(
            "UPDATE creators SET memory_json=? WHERE run_id=? AND creator_id=?",
            (
                json.dumps(memory, ensure_ascii=False, separators=(",", ":")),
                source_run_id,
                creator_id,
            ),
        )


def _compact_click_history(
    *, source: sqlite3.Connection, run_id: str, through_day: int
) -> dict[tuple[str, str], tuple[int, int]]:
    result: dict[tuple[str, str], tuple[int, int]] = {}
    if _table_exists(source, "viewer_click_history"):
        for row in source.execute(
            "SELECT viewer_id,video_id,first_clicked_day,last_clicked_day "
            "FROM viewer_click_history WHERE run_id=? AND last_clicked_day<=?",
            (run_id, through_day),
        ):
            result[(str(row[0]), str(row[1]))] = (int(row[2]), int(row[3]))

    query = (
        "SELECT h.viewer_id,h.simulation_day,h.clicked_video_ids_json "
        "FROM homepage_sessions h JOIN viewers v "
        "ON v.run_id=h.run_id AND v.viewer_id=h.viewer_id "
        "WHERE h.run_id=? AND h.simulation_day<=? AND h.status='succeeded'"
    )
    for viewer_id, day, encoded in source.execute(query, (run_id, through_day)):
        for video_id in json.loads(encoded):
            key = (str(viewer_id), str(video_id))
            previous = result.get(key)
            if previous is None:
                result[key] = (int(day), int(day))
            else:
                result[key] = (min(previous[0], int(day)), max(previous[1], int(day)))
    return result


def _compact_panel_history(
    *, source: sqlite3.Connection, run_id: str, through_day: int
) -> dict[tuple[str, str], int]:
    result: dict[tuple[str, str], int] = {}
    if _table_exists(source, "panel_shown_history"):
        for row in source.execute(
            "SELECT mirror_group_id,video_id,last_shown_day "
            "FROM panel_shown_history WHERE run_id=? AND last_shown_day<=?",
            (run_id, through_day),
        ):
            result[(str(row[0]), str(row[1]))] = int(row[2])

    for group_id, day, encoded in source.execute(
        "SELECT mirror_group_id,simulation_day,candidate_items_json "
        "FROM homepage_sessions WHERE run_id=? AND simulation_day<=? "
        # Match SimulationRunner._panel_shown_history: a planned panel feed
        # remains in shared history even when its model call was skipped.
        "AND mirror_group_id IS NOT NULL",
        (run_id, through_day),
    ):
        for item in json.loads(encoded):
            key = (str(group_id), str(item["video_id"]))
            result[key] = max(result.get(key, -1), int(day))
    return result


def _copy_query(
    *,
    source: sqlite3.Connection,
    destination: sqlite3.Connection,
    table: str,
    query: str,
    parameters: tuple[Any, ...],
) -> int:
    columns = _table_columns(source, table)
    cursor = source.execute(query, parameters)
    placeholders = ",".join("?" for _ in columns)
    insert = (
        f'INSERT INTO {_quote(table)} '
        f'({",".join(_quote(item) for item in columns)}) VALUES ({placeholders})'
    )
    count = 0
    while True:
        rows = cursor.fetchmany(5000)
        if not rows:
            break
        destination.executemany(insert, (tuple(row) for row in rows))
        count += len(rows)
    return count


def _copy_representative_homepages(
    *,
    source: sqlite3.Connection,
    destination: sqlite3.Connection,
    run_id: str,
    through_day: int,
) -> int:
    """Keep playback samples and every strict reference-panel session.

    Population history is represented by one deterministic session per
    day/kind/model stratum.  All reference sessions remain because the matched
    panels are small and analytically important.  Neither set is used as the
    complete click/exposure state; dedicated compact tables carry that truth.
    """

    columns = _table_columns(source, "homepage_sessions")
    cursor = source.execute(
        f'SELECT {",".join(_quote(item) for item in columns)} '
        "FROM homepage_sessions WHERE run_id=? AND simulation_day<=? "
        "AND status='succeeded' ORDER BY simulation_day,viewer_id",
        (run_id, through_day),
    )
    indices = {name: columns.index(name) for name in columns}
    selected: list[tuple[Any, ...]] = []
    seen_population_strata: set[tuple[Any, ...]] = set()
    for row in cursor:
        values = tuple(row)
        if values[indices["cohort_role"]] == "reference":
            selected.append(values)
            continue
        stratum = (
            values[indices["simulation_day"]],
            values[indices["viewer_kind"]],
            values[indices["cohort_role"]],
            values[indices["model_alias"]],
        )
        if stratum not in seen_population_strata:
            seen_population_strata.add(stratum)
            selected.append(values)
    placeholders = ",".join("?" for _ in columns)
    destination.executemany(
        f'INSERT INTO homepage_sessions '
        f'({",".join(_quote(item) for item in columns)}) '
        f'VALUES ({placeholders})',
        selected,
    )
    return len(selected)


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _assert_database_integrity(connection: sqlite3.Connection) -> None:
    check = connection.execute("PRAGMA quick_check").fetchone()
    if check is None or check[0] != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {check}")
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_keys:
        raise RuntimeError(f"SQLite foreign_key_check failed: {foreign_keys[:5]}")


@contextmanager
def _read_only_connection(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _dependency_payloads(config: SimulationConfig) -> tuple[dict[str, bytes], dict[str, str]]:
    """Capture small actor/runtime YAML files, never credential files."""

    result: dict[str, bytes] = {}
    source_hashes: dict[str, str] = {}
    for field in DEPENDENCY_FIELDS:
        path = getattr(config, field)
        if path is None:
            continue
        resolved = Path(path).resolve()
        if not resolved.is_file():
            raise FileNotFoundError(
                f"branch dependency does not exist ({field}): {resolved}"
            )
        payload = resolved.read_bytes()
        source_hashes[field] = hashlib.sha256(payload).hexdigest()
        expected = config.dependency_sha256.get(field)
        if expected is not None and source_hashes[field] != expected:
            raise ValueError("cannot snapshot or fork: external dependencies have drifted")
        # Persona overlays depend on other YAML files. Freeze the *effective*
        # population using the existing loaders, rather than copying a broken
        # relative `extends` pointer or growing a new dependency resolver.
        if field in {"creator_persona_path", "viewer_persona_path"}:
            raw = yaml.safe_load(payload) or {}
            if "extends" in raw:
                from ailiaili.persona import CreatorPopulation, ViewerPopulationSpec

                loader = CreatorPopulation if field == "creator_persona_path" else ViewerPopulationSpec
                payload = yaml.safe_dump(
                    loader.from_yaml(resolved).model_dump(mode="json"),
                    allow_unicode=True, sort_keys=False,
                ).encode("utf-8")
        result[field] = payload
    return result, source_hashes
