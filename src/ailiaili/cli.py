from __future__ import annotations
from pathlib import Path
from typing import Any
import typer
import yaml
from sqlalchemy import func, select
from ailiaili.config import SimulationConfig
from ailiaili.persistence.models import RunRow, VideoRow, HomepageSessionRow, DayRow
from ailiaili.reports.exporter import ReportExporter
from ailiaili.simulation.runner import SimulationRunner
from ailiaili.world_branch import create_world_snapshot, fork_world
from ailiaili.simple_config import prepare_world
from ailiaili.experiment_setup import initialize_world, plan_world, render_plan
app = typer.Typer(name="ailiaili", help="Run and inspect AI content worlds.", no_args_is_help=True)


@app.command("init")
def init_world(
    name: str = typer.Argument(..., help="New run ID."),
    template: str = typer.Option("stories", "--template", help="stories, workshop or discovery"),
    output: Path | None = typer.Option(None, "--output", help="Defaults to configs/local/<name>.yaml"),
) -> None:
    """Create an editable, offline starter experiment. No models are called."""
    try:
        path = initialize_world(name, template, output or Path("configs/local") / f"{name}.yaml")
    except (ValueError, OSError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Created {path}. Preview: python -m ailiaili plan --config \"{path}\"")


@app.command("plan")
def preview_world(config: Path = typer.Option(..., "--config", "-c")) -> None:
    """Explain the configured world and workload without running or spending."""
    try:
        typer.echo(render_plan(plan_world(config)))
    except (ValueError, OSError, yaml.YAMLError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command()
def prepare(
    config: Path = typer.Option(..., "--config", "-c", help="Small custom-world YAML."),
    output: Path | None = typer.Option(None, "--output", help="New directory; defaults to configs/local/<run_id>."),
    check: bool = typer.Option(False, "--check", help="Validate only; no files, database or model calls."),
) -> None:
    """Prepare custom models, creators and categories from one small YAML file."""
    try:
        summary = prepare_world(config, output, check_only=check)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"{summary['run_id']}: {summary['mode']}, {summary['creators']} creators, {summary['viewers']} viewers")
    if check:
        typer.echo("Configuration valid. No files written or model calls made.")
    else:
        typer.echo(f"Prepared. Run: python -m ailiaili run --config \"{summary['config']}\"")


def load_config(path: Path) -> SimulationConfig:
    if not path.exists():
        raise typer.BadParameter(f"config file does not exist: {path}")
    return SimulationConfig.from_yaml(path)


@app.command()
def run(
    config: Path = typer.Option(
        Path("configs/demo.yaml"),
        "--config",
        "-c",
        help="Versioned run configuration.",
    ),
    days: int | None = typer.Option(
        None,
        min=1,
        help="Run through this simulation day; defaults to the config value.",
    ),
) -> None:
    """Initialize a run and advance it through one or more simulation days."""

    simulation_config = load_config(config)
    runner = (
        SimulationRunner.with_llm(simulation_config)
        if simulation_config.execution_mode == "llm"
        else SimulationRunner(simulation_config)
    )
    summaries = runner.run_through(days)
    if not summaries:
        typer.echo("No new days were required; the run is already at the target.")
        return
    for summary in summaries:
        typer.echo(
            f"Day {summary.simulation_day}: "
            f"{summary.impressions} impressions, {summary.clicks} clicks, "
            f"{summary.new_follows} follows, {summary.triples} triples"
        )
    if simulation_config.canonical:
        typer.echo(
            "Report: "
            f"{(simulation_config.run_dir / 'site' / 'index.html').resolve()}"
        )
    else:
        typer.echo(
            "Report projection skipped for this non-canonical branch. "
            f"Generate it on demand with: ailiaili report --config \"{config}\""
        )


@app.command()
def status(
    config: Path = typer.Option(
        Path("configs/demo.yaml"), "--config", "-c"
    ),
) -> None:
    """Show compact status for an existing run."""

    simulation_config = load_config(config)
    runner = SimulationRunner(simulation_config)
    if not simulation_config.database_path.exists():
        typer.echo("Run has not been initialized.")
        raise typer.Exit(code=1)
    runner.database.create_schema()
    with runner.database.session() as session:
        run_row = session.get(RunRow, simulation_config.run_id)
        if run_row is None:
            typer.echo("Run has not been initialized.")
            raise typer.Exit(code=1)
        video_count = session.scalar(
            select(func.count()).select_from(VideoRow).where(
                VideoRow.run_id == simulation_config.run_id
            )
        )
        session_count = session.scalar(
            select(func.count()).select_from(HomepageSessionRow).where(
                HomepageSessionRow.run_id == simulation_config.run_id
            )
        )
        running_days = session.scalars(
            select(DayRow.simulation_day).where(
                DayRow.run_id == simulation_config.run_id,
                DayRow.status == "running",
            )
        ).all()
        typer.echo(f"Run: {run_row.run_id} ({run_row.status})")
        typer.echo(f"Last completed day: {run_row.last_completed_day}")
        typer.echo(f"Videos: {int(video_count or 0)}")
        typer.echo(f"Homepage sessions: {int(session_count or 0)}")
        if running_days:
            typer.echo(f"Running day: {running_days[0]}")


@app.command("retry-incomplete-day")
def retry_incomplete_day(
    config: Path = typer.Option(
        Path("configs/demo.yaml"), "--config", "-c"
    ),
) -> None:
    """Authorize one fresh retry round for technical failures in a blocked day."""

    simulation_config = load_config(config)
    runner = SimulationRunner(simulation_config)
    result = runner.authorize_incomplete_day_retry()
    typer.echo(
        "Authorized technical retry for Day "
        f"{result['simulation_day']}: "
        f"{result['creator_turns_reset']} creator turns, "
        f"{result['viewer_sessions_reset']} viewer sessions, "
        f"{result['logical_calls_authorized']} exhausted logical calls."
    )


@app.command("recover-interrupted-day")
def recover_interrupted_day(
    config: Path = typer.Option(
        Path("configs/demo.yaml"), "--config", "-c"
    ),
    confirm_process_stopped: bool = typer.Option(
        False,
        "--confirm-process-stopped",
        help="Required: the original simulation process can no longer return.",
    ),
) -> None:
    """Abandon uncertain in-flight requests after a hard process stop."""

    if not confirm_process_stopped:
        typer.echo(
            "Refusing recovery: first confirm the original process is stopped, "
            "then pass --confirm-process-stopped."
        )
        raise typer.Exit(code=2)
    simulation_config = load_config(config)
    runner = SimulationRunner(simulation_config)
    result = runner.abandon_indeterminate_attempts()
    typer.echo(
        f"Recovered Day {result['simulation_day']}: marked "
        f"{result['attempts_abandoned']} uncertain attempts as abandoned and "
        f"authorized {result['logical_calls_authorized']} logical calls."
    )


@app.command()
def report(
    config: Path = typer.Option(
        Path("configs/demo.yaml"), "--config", "-c"
    ),
) -> None:
    """Regenerate the static report from completed-day facts."""

    simulation_config = load_config(config)
    runner = SimulationRunner(simulation_config)
    if not simulation_config.database_path.exists():
        typer.echo("Run has not been initialized.")
        raise typer.Exit(code=1)
    path = ReportExporter(simulation_config, runner.database).export()
    typer.echo(f"Report: {path.resolve()}")


@app.command("world-snapshot")
def world_snapshot(
    source_run_dir: Path = typer.Option(
        ...,
        "--source-run-dir",
        help="Run directory containing the authoritative ailiaili.db.",
    ),
    output: Path = typer.Option(
        ...,
        "--output",
        help="New .ailiaili-world.zip snapshot path.",
    ),
    day: int | None = typer.Option(
        None,
        "--day",
        min=0,
        help="Completed branch point; defaults to the latest completed day.",
    ),
) -> None:
    """Package the minimal continuation state of a completed world day."""

    result = create_world_snapshot(
        source_run_dir=source_run_dir,
        output_path=output,
        simulation_day=day,
    )
    typer.echo(
        f"Snapshot: {result.path} | {result.run_id} Day {result.simulation_day} | "
        f"state DB {result.database_bytes / 1024 / 1024:.2f} MB | "
        f"archive {result.archive_bytes / 1024 / 1024:.2f} MB"
    )


@app.command("world-fork")
def world_fork(
    snapshot: Path = typer.Option(
        ...,
        "--snapshot",
        help="A snapshot created by world-snapshot.",
    ),
    run_id: str = typer.Option(..., "--run-id", help="Unique branch Run ID."),
    additional_days: int = typer.Option(
        ...,
        "--additional-days",
        min=1,
        help="How many simulated days this branch may advance.",
    ),
    purpose: str = typer.Option(
        ...,
        "--purpose",
        help="Human-readable single-change experiment purpose.",
    ),
    output_root: Path = typer.Option(Path("runs"), "--output-root"),
    full_evidence: bool = typer.Option(
        False,
        "--full-evidence",
        help="Store complete ordinary Viewer prompts; compact is the default.",
    ),
    set_value: list[str] | None = typer.Option(
        None,
        "--set",
        help=(
            "Validated branch config override as FIELD=YAML_VALUE; repeat for "
            "multiple fields. Identity and population fields are protected."
        ),
    ),
) -> None:
    """Create an isolated non-canonical branch without copying its parent archive."""

    overrides: dict[str, Any] = {}
    for assignment in set_value or []:
        if "=" not in assignment:
            raise typer.BadParameter(
                f"--set expects FIELD=YAML_VALUE, got: {assignment}"
            )
        field, encoded = assignment.split("=", 1)
        field = field.strip()
        if not field:
            raise typer.BadParameter("--set field name cannot be empty")
        overrides[field] = yaml.safe_load(encoded)

    result = fork_world(
        snapshot_path=snapshot,
        run_id=run_id,
        additional_days=additional_days,
        purpose=purpose,
        output_root=output_root,
        evidence_mode="full" if full_evidence else "compact",
        config_overrides=overrides,
    )
    typer.echo(
        f"Branch: {result.run_id} | parent {result.parent_run_id} "
        f"Day {result.parent_day} | target Day {result.target_day}"
    )
    typer.echo(f"Config: {result.config_path}")
    typer.echo(
        f"Run with: ailiaili run --config \"{result.config_path}\""
    )
