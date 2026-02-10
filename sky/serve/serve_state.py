"""The database for services information."""
import collections
import enum
import functools
import json
import pickle
import threading
import typing
from typing import Any, Dict, List, Optional
import uuid

import colorama
import sqlalchemy
from sqlalchemy import exc as sqlalchemy_exc
from sqlalchemy import orm
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext import declarative

from sky.serve import constants
from sky.utils import common_utils
from sky.utils.db import db_utils
from sky.utils.db import migration_utils

if typing.TYPE_CHECKING:
    from sqlalchemy.engine import row

    from sky.serve import replica_managers
    from sky.serve import service_spec

_SQLALCHEMY_ENGINE: Optional[sqlalchemy.engine.Engine] = None
_SQLALCHEMY_ENGINE_LOCK = threading.Lock()

Base = declarative.declarative_base()

# === Database schema ===
services_table = sqlalchemy.Table(
    'services',
    Base.metadata,
    sqlalchemy.Column('name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('controller_job_id',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('controller_port',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('load_balancer_port',
                      sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('status', sqlalchemy.Text),
    sqlalchemy.Column('uptime', sqlalchemy.Integer, server_default=None),
    sqlalchemy.Column('policy', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('auto_restart', sqlalchemy.Integer, server_default=None),
    sqlalchemy.Column('requested_resources',
                      sqlalchemy.LargeBinary,
                      server_default=None),
    sqlalchemy.Column('requested_resources_str', sqlalchemy.Text),
    sqlalchemy.Column('current_version',
                      sqlalchemy.Integer,
                      server_default=str(constants.INITIAL_VERSION)),
    sqlalchemy.Column('active_versions',
                      sqlalchemy.Text,
                      server_default=json.dumps([])),
    sqlalchemy.Column('load_balancing_policy',
                      sqlalchemy.Text,
                      server_default=None),
    sqlalchemy.Column('tls_encrypted', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('pool', sqlalchemy.Integer, server_default='0'),
    sqlalchemy.Column('controller_pid', sqlalchemy.Integer,
                      server_default=None),
    sqlalchemy.Column('hash', sqlalchemy.Text, server_default=None),
    sqlalchemy.Column('entrypoint', sqlalchemy.Text, server_default=None),
)

replicas_table = sqlalchemy.Table(
    'replicas',
    Base.metadata,
    sqlalchemy.Column('service_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('replica_id', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('replica_info', sqlalchemy.LargeBinary),
)

version_specs_table = sqlalchemy.Table(
    'version_specs',
    Base.metadata,
    sqlalchemy.Column('service_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('version', sqlalchemy.Integer, primary_key=True),
    sqlalchemy.Column('spec', sqlalchemy.LargeBinary),
    sqlalchemy.Column('yaml_content', sqlalchemy.Text, server_default=None),
)

serve_ha_recovery_script_table = sqlalchemy.Table(
    'serve_ha_recovery_script',
    Base.metadata,
    sqlalchemy.Column('service_name', sqlalchemy.Text, primary_key=True),
    sqlalchemy.Column('script', sqlalchemy.Text),
)


def create_table(engine: sqlalchemy.engine.Engine):
    """Creates the service and replica tables if they do not exist."""

    # Enable WAL mode to avoid locking issues.
    # See: issue #3863, #1441 and PR #1509
    # https://github.com/microsoft/WSL/issues/2395
    # TODO(romilb): We do not enable WAL for WSL because of known issue in WSL.
    #  This may cause the database locked problem from WSL issue #1441.
    if (engine.dialect.name == db_utils.SQLAlchemyDialect.SQLITE.value and
            not common_utils.is_wsl()):
        try:
            with orm.Session(engine) as session:
                session.execute(sqlalchemy.text('PRAGMA journal_mode=WAL'))
                session.commit()
        except sqlalchemy_exc.OperationalError as e:
            if 'database is locked' not in str(e):
                raise
            # If the database is locked, it is OK to continue, as the WAL mode
            # is not critical and is likely to be enabled by other processes.

    migration_utils.safe_alembic_upgrade(engine, migration_utils.SERVE_DB_NAME,
                                         migration_utils.SERVE_VERSION)


def initialize_and_get_db() -> sqlalchemy.engine.Engine:
    global _SQLALCHEMY_ENGINE

    if _SQLALCHEMY_ENGINE is not None:
        return _SQLALCHEMY_ENGINE

    with _SQLALCHEMY_ENGINE_LOCK:
        if _SQLALCHEMY_ENGINE is not None:
            return _SQLALCHEMY_ENGINE
        # get an engine to the db
        engine = db_utils.get_engine('serve/services')

        # run migrations if needed
        create_table(engine)

        # return engine
        _SQLALCHEMY_ENGINE = engine
        return _SQLALCHEMY_ENGINE


def init_db(func):
    """Initialize the database."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        initialize_and_get_db()
        return func(*args, **kwargs)

    return wrapper


_UNIQUE_CONSTRAINT_FAILED_ERROR_MSGS = [
    # sqlite
    'UNIQUE constraint failed: services.name',
    # postgres
    'duplicate key value violates unique constraint "services_pkey"',
]


# === Statuses ===
class ReplicaStatus(enum.Enum):
    """Replica status."""

    # The `sky.launch` is pending due to max number of simultaneous launches.
    PENDING = 'PENDING'

    # The replica VM is being provisioned. i.e., the `sky.launch` is still
    # running.
    PROVISIONING = 'PROVISIONING'

    # The replica VM is provisioned and the service is starting. This indicates
    # user's `setup` section or `run` section is still running, and the
    # readiness probe fails.
    STARTING = 'STARTING'

    # The replica VM is provisioned and the service is ready, i.e. the
    # readiness probe is passed.
    READY = 'READY'

    # The service was ready before, but it becomes not ready now, i.e. the
    # readiness probe fails.
    NOT_READY = 'NOT_READY'

    # The replica VM is being shut down. i.e., the `sky down` is still running.
    SHUTTING_DOWN = 'SHUTTING_DOWN'

    # The replica fails due to user's run/setup.
    FAILED = 'FAILED'

    # The replica fails due to initial delay exceeded.
    FAILED_INITIAL_DELAY = 'FAILED_INITIAL_DELAY'

    # The replica fails due to healthiness check.
    FAILED_PROBING = 'FAILED_PROBING'

    # The replica fails during launching
    FAILED_PROVISION = 'FAILED_PROVISION'

    # `sky.down` failed during service teardown.
    # This could mean resource leakage.
    # TODO(tian): This status should be removed in the future, at which point
    # we should guarantee no resource leakage like regular sky.
    FAILED_CLEANUP = 'FAILED_CLEANUP'

    # The replica is a spot VM and it is preempted by the cloud provider.
    PREEMPTED = 'PREEMPTED'

    # Unknown. This should never happen (used only for unexpected errors).
    UNKNOWN = 'UNKNOWN'

    @classmethod
    def failed_statuses(cls) -> List['ReplicaStatus']:
        return [
            cls.FAILED, cls.FAILED_CLEANUP, cls.FAILED_INITIAL_DELAY,
            cls.FAILED_PROBING, cls.FAILED_PROVISION, cls.UNKNOWN
        ]

    @classmethod
    def terminal_statuses(cls) -> List['ReplicaStatus']:
        return [cls.SHUTTING_DOWN, cls.PREEMPTED, cls.UNKNOWN
               ] + cls.failed_statuses()

    @classmethod
    def scale_down_decision_order(cls) -> List['ReplicaStatus']:
        # Scale down replicas in the order of replica initialization
        return [
            cls.PENDING, cls.PROVISIONING, cls.STARTING, cls.NOT_READY,
            cls.READY
        ]

    def colored_str(self) -> str:
        color = _REPLICA_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'


_REPLICA_STATUS_TO_COLOR = {
    ReplicaStatus.PENDING: colorama.Fore.YELLOW,
    ReplicaStatus.PROVISIONING: colorama.Fore.BLUE,
    ReplicaStatus.STARTING: colorama.Fore.CYAN,
    ReplicaStatus.READY: colorama.Fore.GREEN,
    ReplicaStatus.NOT_READY: colorama.Fore.YELLOW,
    ReplicaStatus.SHUTTING_DOWN: colorama.Fore.MAGENTA,
    ReplicaStatus.FAILED: colorama.Fore.RED,
    ReplicaStatus.FAILED_INITIAL_DELAY: colorama.Fore.RED,
    ReplicaStatus.FAILED_PROBING: colorama.Fore.RED,
    ReplicaStatus.FAILED_PROVISION: colorama.Fore.RED,
    ReplicaStatus.FAILED_CLEANUP: colorama.Fore.RED,
    ReplicaStatus.PREEMPTED: colorama.Fore.MAGENTA,
    ReplicaStatus.UNKNOWN: colorama.Fore.RED,
}


class ServiceStatus(enum.Enum):
    """Service status as recorded in table 'services'."""

    # Controller is initializing
    CONTROLLER_INIT = 'CONTROLLER_INIT'

    # Replica is initializing and no failure
    REPLICA_INIT = 'REPLICA_INIT'

    # Controller failed to initialize / controller or load balancer process
    # status abnormal
    CONTROLLER_FAILED = 'CONTROLLER_FAILED'

    # At least one replica is ready
    READY = 'READY'

    # Service is being shutting down
    SHUTTING_DOWN = 'SHUTTING_DOWN'

    # At least one replica is failed and no replica is ready
    FAILED = 'FAILED'

    # Clean up failed
    FAILED_CLEANUP = 'FAILED_CLEANUP'

    # No replica
    NO_REPLICA = 'NO_REPLICA'

    @classmethod
    def failed_statuses(cls) -> List['ServiceStatus']:
        return [cls.CONTROLLER_FAILED, cls.FAILED_CLEANUP]

    @classmethod
    def refuse_to_terminate_statuses(cls) -> List['ServiceStatus']:
        return [cls.CONTROLLER_FAILED, cls.FAILED_CLEANUP, cls.SHUTTING_DOWN]

    def colored_str(self) -> str:
        color = _SERVICE_STATUS_TO_COLOR[self]
        return f'{color}{self.value}{colorama.Style.RESET_ALL}'

    @classmethod
    def from_replica_statuses(
            cls, replica_statuses: List[ReplicaStatus]) -> 'ServiceStatus':
        status2num = collections.Counter(replica_statuses)
        # If one replica is READY, the service is READY.
        if status2num[ReplicaStatus.READY] > 0:
            return cls.READY
        if sum(status2num[status]
               for status in ReplicaStatus.failed_statuses()) > 0:
            return cls.FAILED
        # When min_replicas = 0, there is no (provisioning) replica.
        if not replica_statuses:
            return cls.NO_REPLICA
        return cls.REPLICA_INIT


_SERVICE_STATUS_TO_COLOR = {
    ServiceStatus.CONTROLLER_INIT: colorama.Fore.BLUE,
    ServiceStatus.REPLICA_INIT: colorama.Fore.BLUE,
    ServiceStatus.CONTROLLER_FAILED: colorama.Fore.RED,
    ServiceStatus.READY: colorama.Fore.GREEN,
    ServiceStatus.SHUTTING_DOWN: colorama.Fore.YELLOW,
    ServiceStatus.FAILED: colorama.Fore.RED,
    ServiceStatus.FAILED_CLEANUP: colorama.Fore.RED,
    ServiceStatus.NO_REPLICA: colorama.Fore.MAGENTA,
}


@init_db
def add_service(name: str, controller_job_id: int, policy: str,
                requested_resources_str: str, load_balancing_policy: str,
                status: ServiceStatus, tls_encrypted: bool, pool: bool,
                controller_pid: int, entrypoint: str) -> bool:
    """Add a service in the database.

    Returns:
        True if the service is added successfully, False if the service already
        exists.
    """
    assert _SQLALCHEMY_ENGINE is not None
    try:
        with orm.Session(_SQLALCHEMY_ENGINE) as session:
            if (_SQLALCHEMY_ENGINE.dialect.name ==
                    db_utils.SQLAlchemyDialect.SQLITE.value):
                insert_func = sqlite.insert
            elif (_SQLALCHEMY_ENGINE.dialect.name ==
                  db_utils.SQLAlchemyDialect.POSTGRESQL.value):
                insert_func = postgresql.insert
            else:
                raise ValueError('Unsupported database dialect')

            insert_stmt = insert_func(services_table).values(
                name=name,
                controller_job_id=controller_job_id,
                status=status.value,
                policy=policy,
                requested_resources_str=requested_resources_str,
                load_balancing_policy=load_balancing_policy,
                tls_encrypted=int(tls_encrypted),
                pool=int(pool),
                controller_pid=controller_pid,
                hash=str(uuid.uuid4()),
                entrypoint=entrypoint)
            session.execute(insert_stmt)
            session.commit()

    except sqlalchemy_exc.IntegrityError as e:
        for msg in _UNIQUE_CONSTRAINT_FAILED_ERROR_MSGS:
            if msg in str(e):
                return False
        raise RuntimeError('Unexpected database error') from e
    return True


@init_db
def update_service_controller_pid(service_name: str,
                                  controller_pid: int) -> None:
    """Updates the controller pid of a service.

    This is used to update the controller pid of a service on ha recovery.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(services_table).filter(
            services_table.c.name == service_name).update(
                {services_table.c.controller_pid: controller_pid})
        session.commit()


@init_db
def remove_service(service_name: str) -> None:
    """Removes a service from the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(
            sqlalchemy.delete(services_table).where(
                services_table.c.name == service_name))
        session.commit()


@init_db
def set_service_uptime(service_name: str, uptime: int) -> None:
    """Sets the uptime of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(services_table).filter(
            services_table.c.name == service_name).update(
                {services_table.c.uptime: uptime})
        session.commit()


@init_db
def set_service_status_and_active_versions(
        service_name: str,
        status: ServiceStatus,
        active_versions: Optional[List[int]] = None) -> None:
    """Sets the service status."""
    assert _SQLALCHEMY_ENGINE is not None
    update_dict = {services_table.c.status: status.value}
    if active_versions is not None:
        update_dict[services_table.c.active_versions] = json.dumps(
            active_versions)

    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(services_table).filter(
            services_table.c.name == service_name).update(update_dict)
        session.commit()


@init_db
def set_service_controller_port(service_name: str,
                                controller_port: int) -> None:
    """Sets the controller port of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(services_table).filter(
            services_table.c.name == service_name).update(
                {services_table.c.controller_port: controller_port})
        session.commit()


@init_db
def set_service_load_balancer_port(service_name: str,
                                   load_balancer_port: int) -> None:
    """Sets the load balancer port of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.query(services_table).filter(
            services_table.c.name == service_name).update(
                {services_table.c.load_balancer_port: load_balancer_port})
        session.commit()


def _get_service_from_row(r: 'row.RowMapping') -> Dict[str, Any]:
    # Get the max_version from the first column (from the subquery)
    current_version = r['max_version']

    record = {
        'name': r['name'],
        'controller_job_id': r['controller_job_id'],
        'controller_port': r['controller_port'],
        'load_balancer_port': r['load_balancer_port'],
        'status': ServiceStatus[r['status']],
        'uptime': r['uptime'],
        'policy': r['policy'],
        # The version of the autoscaler/replica manager are on. It can be larger
        # than the active versions as the load balancer may not consider the
        # latest version to be active for serving traffic.
        'version': current_version,
        # The versions that is active for the load balancer. This is a list of
        # integers in json format. This is mainly for display purpose.
        'active_versions': json.loads(r['active_versions'])
                           if r['active_versions'] else [],
        'requested_resources_str': r['requested_resources_str'],
        'load_balancing_policy': r['load_balancing_policy'],
        'tls_encrypted': bool(r['tls_encrypted']),
        'pool': bool(r['pool']),
        'controller_pid': r['controller_pid'],
        'hash': r['hash'],
        'entrypoint': r['entrypoint'],
        'yaml_content': r.get('yaml_content'),
    }
    latest_spec = get_spec(r['name'], current_version)
    if latest_spec is not None:
        record['policy'] = latest_spec.autoscaling_policy_str()
        record['load_balancing_policy'] = latest_spec.load_balancing_policy
    return record


def _build_services_with_latest_version_query(
        service_name: Optional[str] = None) -> sqlalchemy.sql.Select:
    """Builds a query joining services with their latest version and yaml.

    Args:
        service_name: If provided, filter to this service only.

    Returns:
        A SQLAlchemy selectable for fetching rows, including columns:
        - max_version (latest version per service)
        - services_table.*
        - yaml_content (from version_specs_table for latest version)
    """
    subquery = sqlalchemy.select(
        version_specs_table.c.service_name,
        sqlalchemy.func.max(version_specs_table.c.version).label('max_version'),
    ).group_by(version_specs_table.c.service_name).alias('v')

    query = sqlalchemy.select(
        subquery.c.max_version,
        services_table,
        version_specs_table.c.yaml_content,
    ).select_from(
        services_table.join(
            subquery, services_table.c.name == subquery.c.service_name).join(
                version_specs_table,
                sqlalchemy.and_(
                    version_specs_table.c.service_name == services_table.c.name,
                    version_specs_table.c.version == subquery.c.max_version,
                ),
            ))
    if service_name is not None:
        query = query.where(services_table.c.name == service_name)
    return query


@init_db
def get_services() -> List[Dict[str, Any]]:
    """Get all existing service records."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = _build_services_with_latest_version_query()
        rows = session.execute(query).fetchall()
    records = []
    for row in rows:
        records.append(_get_service_from_row(row._mapping))  # pylint: disable=protected-access
    return records


@init_db
def get_num_services() -> int:
    """Get the number of services."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        return session.execute(
            sqlalchemy.select(sqlalchemy.func.count()  # pylint: disable=not-callable
                             ).select_from(services_table)).fetchone()[0]


@init_db
def get_service_from_name(service_name: str) -> Optional[Dict[str, Any]]:
    """Get all existing service records."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        query = _build_services_with_latest_version_query(service_name)
        rows = session.execute(query).fetchall()
    for row in rows:
        return _get_service_from_row(row._mapping)  # pylint: disable=protected-access
    return None


@init_db
def get_service_hash(service_name: str) -> Optional[str]:
    """Get the hash of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(services_table.c.hash).where(
                services_table.c.name == service_name)).fetchone()
    return result[0] if result else None


@init_db
def get_service_versions(service_name: str) -> List[int]:
    """Gets all versions of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(
            sqlalchemy.select(version_specs_table.c.version.distinct()).where(
                version_specs_table.c.service_name == service_name)).fetchall()
    return [row[0] for row in rows]


@init_db
def get_glob_service_names(
        service_names: Optional[List[str]] = None) -> List[str]:
    """Get service names matching the glob patterns.

    Args:
        service_names: A list of glob patterns. If None, return all service
            names.

    Returns:
        A list of non-duplicated service names.
    """
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if service_names is None:
            rows = session.execute(sqlalchemy.select(
                services_table.c.name)).fetchall()
        else:
            rows = []
            for service_name in service_names:
                pattern_rows = session.execute(
                    sqlalchemy.select(services_table.c.name).where(
                        services_table.c.name.like(
                            service_name.replace('*', '%')))).fetchall()
                rows.extend(pattern_rows)
    return list({row[0] for row in rows})


# === Replica functions ===
@init_db
def add_or_update_replica(service_name: str, replica_id: int,
                          replica_info: 'replica_managers.ReplicaInfo') -> None:
    """Adds a replica to the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmt = insert_func(replicas_table).values(
            service_name=service_name,
            replica_id=replica_id,
            replica_info=pickle.dumps(replica_info))

        insert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=['service_name', 'replica_id'],
            set_={'replica_info': insert_stmt.excluded.replica_info})

        session.execute(insert_stmt)
        session.commit()


@init_db
def remove_replica(service_name: str, replica_id: int) -> None:
    """Removes a replica from the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(
            sqlalchemy.delete(replicas_table).where(
                sqlalchemy.and_(replicas_table.c.service_name == service_name,
                                replicas_table.c.replica_id == replica_id)))
        session.commit()


@init_db
def get_replica_info_from_id(
        service_name: str,
        replica_id: int) -> Optional['replica_managers.ReplicaInfo']:
    """Gets a replica info from the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(replicas_table.c.replica_info).where(
                sqlalchemy.and_(
                    replicas_table.c.service_name == service_name,
                    replicas_table.c.replica_id == replica_id))).fetchone()
    return pickle.loads(result[0]) if result else None


@init_db
def get_replica_infos(
        service_name: str) -> List['replica_managers.ReplicaInfo']:
    """Gets all replica infos of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(
            sqlalchemy.select(replicas_table.c.replica_info).where(
                replicas_table.c.service_name == service_name)).fetchall()
    return [pickle.loads(row[0]) for row in rows]


@init_db
def total_number_provisioning_replicas() -> int:
    """Returns the total number of provisioning replicas."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(sqlalchemy.select(
            replicas_table.c.replica_info)).fetchall()
    provisioning_count = 0
    for row in rows:
        replica_info: 'replica_managers.ReplicaInfo' = pickle.loads(row[0])
        if replica_info.status == ReplicaStatus.PROVISIONING:
            provisioning_count += 1
    return provisioning_count


@init_db
def total_number_terminating_replicas() -> int:
    """Returns the total number of terminating replicas."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        rows = session.execute(sqlalchemy.select(
            replicas_table.c.replica_info)).fetchall()
    terminating_count = 0
    for row in rows:
        replica_info: 'replica_managers.ReplicaInfo' = pickle.loads(row[0])
        if (replica_info.status_property.sky_down_status ==
                common_utils.ProcessStatus.RUNNING):
            terminating_count += 1
    return terminating_count


def get_replicas_at_status(
    service_name: str,
    status: ReplicaStatus,
) -> List['replica_managers.ReplicaInfo']:
    replicas = get_replica_infos(service_name)
    return [replica for replica in replicas if replica.status == status]


# === Version functions ===
@init_db
def add_version(service_name: str) -> int:
    """Adds a version to the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        # Insert new version with MAX(version) + 1 in a single atomic operation
        max_version_subquery = sqlalchemy.select(
            sqlalchemy.func.coalesce(
                sqlalchemy.func.max(version_specs_table.c.version), 0) +
            1).where(version_specs_table.c.service_name ==
                     service_name).scalar_subquery()

        # Use INSERT with subquery and RETURNING
        insert_stmt = sqlalchemy.insert(version_specs_table).values(
            service_name=service_name,
            version=max_version_subquery,
            spec=pickle.dumps(None)).returning(version_specs_table.c.version)

        result = session.execute(insert_stmt)
        new_version = result.scalar()
        session.commit()
    return new_version


@init_db
def add_or_update_version(service_name: str, version: int,
                          spec: 'service_spec.SkyServiceSpec',
                          yaml_content: str) -> None:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmt = insert_func(version_specs_table).values(
            service_name=service_name,
            version=version,
            spec=pickle.dumps(spec),
            yaml_content=yaml_content)

        insert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=['service_name', 'version'],
            set_={
                'spec': insert_stmt.excluded.spec,
                'yaml_content': insert_stmt.excluded.yaml_content
            })

        session.execute(insert_stmt)
        session.commit()


@init_db
def get_spec(service_name: str,
             version: int) -> Optional['service_spec.SkyServiceSpec']:
    """Gets spec from the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(version_specs_table.c.spec).where(
                sqlalchemy.and_(
                    version_specs_table.c.service_name == service_name,
                    version_specs_table.c.version == version))).fetchone()
    return pickle.loads(result[0]) if result else None


@init_db
def get_yaml_content(service_name: str, version: int) -> Optional[str]:
    """Gets the yaml content of a version."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(version_specs_table.c.yaml_content).where(
                sqlalchemy.and_(
                    version_specs_table.c.service_name == service_name,
                    version_specs_table.c.version == version))).fetchone()
    return result[0] if result else None


@init_db
def delete_version(service_name: str, version: int) -> None:
    """Deletes a version from the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(
            sqlalchemy.delete(version_specs_table).where(
                sqlalchemy.and_(
                    version_specs_table.c.service_name == service_name,
                    version_specs_table.c.version == version)))
        session.commit()


@init_db
def delete_all_versions(service_name: str) -> None:
    """Deletes all versions from the database."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(
            sqlalchemy.delete(version_specs_table).where(
                version_specs_table.c.service_name == service_name))
        session.commit()


@init_db
def get_latest_version(service_name: str) -> Optional[int]:
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(sqlalchemy.func.max(
                version_specs_table.c.version)).where(
                    version_specs_table.c.service_name ==
                    service_name)).fetchone()
    return result[0] if result else None


@init_db
def get_service_controller_port(service_name: str) -> int:
    """Gets the controller port of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(services_table.c.controller_port).where(
                services_table.c.name == service_name)).fetchone()
        if result is None:
            raise ValueError(f'Service {service_name} does not exist.')
        return result[0]


@init_db
def get_service_load_balancer_port(service_name: str) -> int:
    """Gets the load balancer port of a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(services_table.c.load_balancer_port).where(
                services_table.c.name == service_name)).fetchone()
        if result is None:
            raise ValueError(f'Service {service_name} does not exist.')
        return result[0]


@init_db
def get_ha_recovery_script(service_name: str) -> Optional[str]:
    """Gets the HA recovery script for a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        result = session.execute(
            sqlalchemy.select(serve_ha_recovery_script_table.c.script).where(
                serve_ha_recovery_script_table.c.service_name ==
                service_name)).fetchone()
    return result[0] if result else None


@init_db
def set_ha_recovery_script(service_name: str, script: str) -> None:
    """Sets the HA recovery script for a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        if (_SQLALCHEMY_ENGINE.dialect.name ==
                db_utils.SQLAlchemyDialect.SQLITE.value):
            insert_func = sqlite.insert
        elif (_SQLALCHEMY_ENGINE.dialect.name ==
              db_utils.SQLAlchemyDialect.POSTGRESQL.value):
            insert_func = postgresql.insert
        else:
            raise ValueError('Unsupported database dialect')

        insert_stmt = insert_func(serve_ha_recovery_script_table).values(
            service_name=service_name, script=script)

        insert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=['service_name'],
            set_={'script': insert_stmt.excluded.script})

        session.execute(insert_stmt)
        session.commit()


@init_db
def remove_ha_recovery_script(service_name: str) -> None:
    """Removes the HA recovery script for a service."""
    assert _SQLALCHEMY_ENGINE is not None
    with orm.Session(_SQLALCHEMY_ENGINE) as session:
        session.execute(
            sqlalchemy.delete(serve_ha_recovery_script_table).where(
                serve_ha_recovery_script_table.c.service_name == service_name))
        session.commit()
