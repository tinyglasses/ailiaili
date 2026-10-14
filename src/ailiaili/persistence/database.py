from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, event, inspect, text
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.orm import Session, sessionmaker

from ailiaili.persistence.models import Base


def create_engine(database_path: Path) -> Engine:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = sqlalchemy_create_engine(
        f"sqlite:///{database_path.as_posix()}",
        future=True,
        # Formal viewer stages can have up to 200 short-lived workers.  The
        # SQL work itself remains tiny and SQLite still serializes writers,
        # but the default 5+10 QueuePool can be exhausted by workers waiting
        # for that writer slot.  Match the declared concurrency ceiling so a
        # harmless read does not crash the whole day while connections wait.
        pool_size=64,
        max_overflow=192,
        pool_timeout=60,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


class Database:
    def __init__(self, database_path: Path):
        self.path = database_path
        self.engine = create_engine(database_path)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)
        # The project intentionally avoids a migration framework in v0.x, but
        # these additive nullable/defaulted columns keep earlier run databases
        # readable after model-persona comparison metadata was introduced.
        with self.engine.begin() as connection:
            schema = inspect(connection)
            creator_columns = {
                item["name"] for item in schema.get_columns("creators")
            }
            if "mirror_group_id" not in creator_columns:
                connection.execute(
                    text(
                        "ALTER TABLE creators ADD COLUMN "
                        "mirror_group_id VARCHAR(80)"
                    )
                )
            if "mirror_slot" not in creator_columns:
                connection.execute(
                    text("ALTER TABLE creators ADD COLUMN mirror_slot INTEGER")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_creators_mirror_group_id "
                    "ON creators (mirror_group_id)"
                )
            )

            homepage_columns = {
                item["name"] for item in schema.get_columns("homepage_sessions")
            }
            if "cohort_role" not in homepage_columns:
                connection.execute(
                    text(
                        "ALTER TABLE homepage_sessions ADD COLUMN "
                        "cohort_role VARCHAR(20) NOT NULL DEFAULT 'population'"
                    )
                )

            creator_turn_columns = {
                item["name"] for item in schema.get_columns("creator_turns")
            }
            if "expected_reception" not in creator_turn_columns:
                connection.execute(
                    text(
                        "ALTER TABLE creator_turns ADD COLUMN "
                        "expected_reception VARCHAR(20)"
                    )
                )
            if "prediction_reason" not in creator_turn_columns:
                connection.execute(
                    text(
                        "ALTER TABLE creator_turns ADD COLUMN prediction_reason TEXT"
                    )
                )

            video_columns = {
                item["name"] for item in schema.get_columns("videos")
            }
            if "grounding_mode" not in video_columns:
                connection.execute(
                    text(
                        "ALTER TABLE videos ADD COLUMN grounding_mode "
                        "VARCHAR(30) NOT NULL DEFAULT 'synthesis'"
                    )
                )

            attempt_columns = {
                item["name"] for item in schema.get_columns("llm_attempts")
            }
            if "resolved_provider" not in attempt_columns:
                connection.execute(
                    text(
                        "ALTER TABLE llm_attempts ADD COLUMN "
                        "resolved_provider VARCHAR(80)"
                    )
                )

    def session(self) -> Session:
        return self.session_factory()
