"""Permission service for SkyPilot API Server."""
import os
import threading
import time
from typing import List

import casbin
from casbin.persist import adapters
from sqlalchemy import text
import sqlalchemy_adapter

from sky import global_user_state
from sky import sky_logging
from sky.users import rbac

logger = sky_logging.init_logger(__name__)

_instance = None
_lock = threading.Lock()


class SQLAlchemyWatcher(adapters.UpdateAdapter):
    """Watcher for SQLAlchemy adapter to notify policy changes."""

    def __init__(self, adapter, engine):
        self.adapter = adapter
        self.engine = engine
        self.callbacks = []
        self.last_update_time = int(time.time())
        self._stop_event = threading.Event()
        self._watch_thread = threading.Thread(target=self._watch_loop,
                                              daemon=True)
        self._watch_thread.start()

    def set_update_callback(self, callback):
        """Set callback function to be called when policy is updated."""
        self.callbacks.append(callback)

    def update(self):
        """Notify all callbacks that policy has been updated."""
        # Update the timestamp in the database
        current_timestamp = int(time.time())
        with self.engine.connect() as conn:
            conn.execute(
                text("""
                INSERT INTO policy_update_time (updated_at) 
                VALUES (:timestamp)
            """), {'timestamp': current_timestamp})
            conn.commit()

    def _watch_loop(self):
        """Watch for policy changes in database."""
        while not self._stop_event.is_set():
            try:
                with self.engine.connect() as conn:
                    result = conn.execute(
                        text("""
                        SELECT MAX(updated_at) as last_update 
                        FROM policy_update_time
                    """))
                    last_update = result.scalar()
                    if last_update and last_update > self.last_update_time:
                        logger.debug('Policy change detected in database')
                        self.last_update_time = int(last_update)
                        for callback in self.callbacks:
                            callback()
            except Exception as e:  # pylint: disable=broad-except
                logger.error(f'Error in watch loop: {e}')

            time.sleep(1)

    def stop(self):
        """Stop the watcher thread."""
        self._stop_event.set()
        if self._watch_thread.is_alive():
            self._watch_thread.join()


class PermissionService:
    """Permission service for SkyPilot API Server."""

    def __init__(self):
        global _instance
        if _instance is None:
            with _lock:
                if _instance is None:
                    _instance = self
                    engine = global_user_state.SQLALCHEMY_ENGINE
                    adapter = sqlalchemy_adapter.Adapter(engine)
                    watcher = SQLAlchemyWatcher(adapter, engine)
                    model_path = os.path.join(os.path.dirname(__file__),
                                              'model.conf')
                    enforcer = casbin.Enforcer(model_path, adapter)
                    enforcer.set_watcher(watcher)
                    self.enforcer = enforcer
                    self.watcher = watcher
        else:
            self.enforcer = _instance.enforcer
            self.watcher = _instance.watcher

    def __del__(self):
        """Cleanup when instance is destroyed."""
        if hasattr(self, 'watcher'):
            self.watcher.stop()

    def init_policies(self):
        """Initialize policies."""
        logger.debug('Initializing policies')
        # Only clear p policies (permission policies),
        # keep g policies (role policies)
        self.enforcer.remove_filtered_policy(0)
        for role, permissions in rbac.get_role_permissions().items():
            if permissions['permissions'] and 'blocklist' in permissions[
                    'permissions']:
                blocklist = permissions['permissions']['blocklist']
                for item in blocklist:
                    path = item['path']
                    method = item['method']
                    self.enforcer.add_policy(role, path, method)
        all_users = global_user_state.get_all_users()
        for user in all_users:
            user_roles = self.get_user_roles(user.id)
            if len(user_roles) == 0:
                logger.info(f'User {user.id} has no roles, adding'
                            f' default role {rbac.get_default_role()}')
                self.enforcer.add_grouping_policy(user.id,
                                                  rbac.get_default_role())
        self.enforcer.save_policy()

    def add_role(self, user: str, role: str):
        """Add user role relationship."""
        self.enforcer.add_grouping_policy(user, role)
        self.enforcer.save_policy()
        # Notify other clients to update their policies
        self.watcher.update()

    def remove_role(self, user: str, role: str):
        """Remove user role relationship."""
        self.enforcer.remove_grouping_policy(user, role)
        self.enforcer.save_policy()
        self.watcher.update()

    def update_role(self, user: str, new_role: str):
        """Update user role relationship."""
        with _lock:
            # Get current roles
            current_roles = self.get_user_roles(user)
            if not current_roles:
                logger.warning(f'User {user} has no roles')
            else:
                # TODO(hailong): how to handle multiple roles?
                current_role = current_roles[0]
                if current_role == new_role:
                    logger.info(f'User {user} already has role {new_role}')
                    return
                self.enforcer.remove_grouping_policy(user, current_role)

            # Update user role
            self.enforcer.add_grouping_policy(user, new_role)
            self.enforcer.save_policy()
            self.watcher.update()

    def get_user_roles(self, user: str) -> List[str]:
        """Get all roles for a user.

        This method returns all roles that the user has, including inherited
        roles. For example, if a user has role 'admin' and 'admin' inherits
        from 'user', this method will return ['admin', 'user'].

        Args:
            user: The user ID to get roles for.

        Returns:
            A list of role names that the user has.
        """
        return self.enforcer.get_roles_for_user(user)

    def check_permission(self, user: str, path: str, method: str) -> bool:
        """Check permission."""
        return self.enforcer.enforce(user, path, method)

    def load_policy(self):
        """Load policy from storage."""
        self.enforcer.load_policy()
