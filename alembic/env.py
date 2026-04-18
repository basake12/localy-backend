from logging.config import fileConfig
from sqlalchemy import text
from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
import geoalchemy2  # noqa: F401

from app.core.database import Base
import app.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


POSTGIS_TABLES = {
    "spatial_ref_sys", "geography_columns", "geometry_columns",
    "raster_columns", "raster_overviews",
}

def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    return True


def render_item(type_, obj, autogen_context):
    """
    Inject 'import geoalchemy2' into every generated migration file.
    Without this, Alembic renders Geography columns as geoalchemy2.types.Geography(...)
    but never adds the import, causing NameError at upgrade time.
    """
    if type_ == "type" and isinstance(obj, geoalchemy2.types.Geography):
        autogen_context.imports.add("import geoalchemy2")
    # Return False to let Alembic use its default rendering for everything else
    return False


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_item=render_item,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Ensure PostGIS and uuid-ossp exist before any migration DDL runs
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis;"))
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_item=render_item,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
