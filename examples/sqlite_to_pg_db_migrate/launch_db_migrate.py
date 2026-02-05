#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "kubernetes",
# ]
# ///

import argparse
import base64
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import sys
import time

from kubernetes import client
from kubernetes import config
from kubernetes.client.models import V1Deployment
from kubernetes.client.models import V1Service
from kubernetes.client.rest import ApiException
from kubernetes.stream import stream

K8S_NAMESPACE = os.environ.get("SKYPILOT_API_NAMESPACE", "skypilot")
K8S_CONTEXT = os.environ.get("SKYPILOT_API_CONTEXT")
SERVICE_NAME = os.environ.get("SKYPILOT_API_SERVICE", "skypilot-api-service")
DEPLOYMENT_NAME = os.environ.get("SKYPILOT_API_DEPLOYMENT",
                                 "skypilot-api-server")
STATE_VOLUME = os.environ.get("SKYPILOT_API_STATE_VOLUME", "skypilot-state")
SNAPSHOT_CLASS_NAME = os.environ.get("SKYPILOT_SNAPSHOT_CLASS")
CONFIG_CONFIGMAP_NAME = os.environ.get("SKYPILOT_API_CONFIGMAP",
                                       "skypilot-config")
SCRIPT_CONFIGMAP_NAME = os.environ.get("SKYPILOT_API_MIGRATE_CONFIGMAP",
                                       "sql-migrate-script")
DRY_RUN = os.environ.get("DRY_RUN", "none")
WAIT_TIME = int(os.environ.get("WAIT_TIME", "5"))


class SkyDbMigrationLauncher:

    def __init__(self,
                 context: str | None = K8S_CONTEXT,
                 namespace: str = K8S_NAMESPACE,
                 service_name: str = SERVICE_NAME,
                 deployment_name: str = DEPLOYMENT_NAME,
                 state_volume_name: str = STATE_VOLUME,
                 snapshot_class_name: str | None = SNAPSHOT_CLASS_NAME,
                 config_configmap_name: str = CONFIG_CONFIGMAP_NAME,
                 script_configmap_name: str = SCRIPT_CONFIGMAP_NAME,
                 dry_run: str = DRY_RUN):
        self.context = context
        self.namespace = namespace
        self.service_name = service_name
        self.deployment_name = deployment_name
        self.state_volume_name = state_volume_name
        self.snapshot_class_name = snapshot_class_name
        self.script_configmap_name = script_configmap_name
        self.config_configmap_name = config_configmap_name
        self.dry_run = dry_run
        self.k8s_dry_run = 'All' if self.dry_run == 'k8s' else None

        # Load kubeconfig
        try:
            config.load_kube_config(context=self.context)
        except Exception as e:
            print(f"Error loading kubeconfig: {e}")
            sys.exit(1)

        # Create API clients
        self.api_client = client.ApiClient()
        self.custom_api = client.CustomObjectsApi(self.api_client)
        self.core_api = client.CoreV1Api(self.api_client)
        self.networking_api = client.NetworkingV1Api(self.api_client)
        self.apps_api = client.AppsV1Api(self.api_client)

        # Get paths
        script_dir = Path(__file__).parent.resolve()
        self.migrate_script_path = script_dir / "sky_sql_migrate.py"

        if not self.migrate_script_path.exists():
            print(f"Migration script not found: {self.migrate_script_path}")
            sys.exit(1)

        # values filled later
        self.service: V1Service | None = None
        self.snapshot_name = None
        self.start_time = None
        self.start_time_str = None
        self.original_deployment: V1Deployment | None = None
        self.cleaned = False

    def create_volume_snapshot(self):

        # Generate snapshot date
        snapshot_date = self.start_time_str.replace(':', '-').lower()

        self.snapshot_name = f"sky-db-backup-{snapshot_date}"

        print(f"Creating volume snapshot: {self.snapshot_name}")

        snapshot_body = {
            "apiVersion": "snapshot.storage.k8s.io/v1",
            "kind": "VolumeSnapshot",
            "metadata": {
                "name": self.snapshot_name
            },
            "spec": {
                "source": {
                    "persistentVolumeClaimName": self.state_volume_name,
                }
            }
        }
        if self.snapshot_class_name is None:
            print("No snapshot class specified, using default")
        else:
            snapshot_body["spec"][
                "volumeSnapshotClassName"] = self.snapshot_class_name

        print(f"creating snapshot: {snapshot_body}")

        try:
            self.custom_api.create_namespaced_custom_object(
                group="snapshot.storage.k8s.io",
                version="v1",
                namespace=self.namespace,
                plural="volumesnapshots",
                body=snapshot_body,
                dry_run=self.k8s_dry_run,
            )
        except ApiException as e:
            print(f"Error creating volume snapshot: {e}")
            sys.exit(1)

    def delete_migration_configmap(self):
        # Delete existing configmap if it exists
        try:
            self.core_api.delete_namespaced_config_map(
                name=self.script_configmap_name,
                namespace=self.namespace,
                dry_run=self.k8s_dry_run,
            )
            print(f"Deleted existing configmap: {self.script_configmap_name}")
        except ApiException as e:
            if e.status != 404:
                print(f"Error deleting configmap: {e}")

    def create_migration_configmap(self):
        print("Creating migration script configmap")

        # Read the migration script
        with open(self.migrate_script_path, 'r') as f:
            script_content = f.read()

        dry_run = 'All' if self.dry_run == 'k8s' else None

        self.delete_migration_configmap()

        # Create new configmap
        configmap = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=self.script_configmap_name,),
            data={"sky_sql_migrate.py": script_content})

        try:
            self.core_api.create_namespaced_config_map(
                namespace=self.namespace,
                body=configmap,
                dry_run=self.k8s_dry_run,
            )
            print(f"Created configmap: {self.script_configmap_name}")
        except ApiException as e:
            if e.status == 409 and self.dry_run == 'k8s':
                print(
                    f"dry run: Configmap already exists, ok: {self.script_configmap_name}"
                )
                return
            print(f"Error creating configmap: {e}")
            sys.exit(1)

    def _toggle_service_off(self):
        assert self.service is None, "Can't toggle off with service stored"
        try:
            self.service = self.core_api.read_namespaced_service(
                name=self.service_name,
                namespace=self.namespace,
            )
        except ApiException as e:
            print(f"Error toggling service during read: {e}")
            sys.exit(1)

        try:
            self.core_api.delete_namespaced_service(
                name=self.service_name,
                namespace=self.namespace,
                dry_run=self.k8s_dry_run,
            )
        except ApiException as e:
            print(f"Error toggling service during delete: {e}")
            sys.exit(1)

        print(f"Deleted service: {self.service}")

    def _toggle_service_on(self):
        assert self.service is not None, "Can't toggle on without service stored"
        self.service.metadata.resource_version = None
        try:
            self.core_api.create_namespaced_service(
                namespace=self.namespace,
                body=self.service,
                dry_run=self.k8s_dry_run,
            )
            self.service = None
        except ApiException as e:
            if e.status == 409 and self.dry_run == 'k8s':
                print(
                    f"dry run: service already exists, ok: {self.service_name}")
                return
            print(f"Error toggling service during create: {e}")
            sys.exit(1)

    def toggle_service(self):
        if self.service is None:
            self._toggle_service_off()
        else:
            self._toggle_service_on()

    def get_deployment(self) -> V1Deployment:
        try:
            deployment = self.apps_api.read_namespaced_deployment(
                name=self.deployment_name,
                namespace=self.namespace,
            )
            return deployment
        except ApiException as e:
            print(f"Error getting deployment: {e}")
            sys.exit(1)

    def update_deployment_for_migration(self):
        print("Updating deployment to run migration...")

        migrate_args = [
            "/root/.local/bin/uv", "run", "--script",
            "/migrate/sky_sql_migrate.py"
        ]

        if self.dry_run == 'db':
            migrate_args.append("--dry-run")

        updated_deployment = copy.deepcopy(self.original_deployment)

        container_env = updated_deployment.spec.template.spec.containers[0].env
        future_db_env_var_present = False
        for env_var in container_env:
            if env_var.name == "FUTURE_SKYPILOT_DB_CONNECTION_URI":
                future_db_env_var_present = True
                break

        if not future_db_env_var_present:
            print(
                "Set FUTURE_SKYPILOT_DB_CONNECTION_URI env var in your pod to expected postgres URI"
            )
            sys.exit(1)

        # add migration args
        updated_deployment.spec.template.spec.containers[0].args = migrate_args

        # add migrate script volume
        updated_deployment.spec.template.spec.volumes.append({
            "name": "migrate-script",
            "configMap": {
                "name": self.script_configmap_name,
            }
        })

        # mount migrate script volume
        updated_deployment.spec.template.spec.containers[
            0].volume_mounts.append({
                "name": "migrate-script",
                "mountPath": "/migrate"
            })

        updated_deployment.metadata.resource_version = None

        try:
            self.apps_api.replace_namespaced_deployment(
                name=self.deployment_name,
                namespace=self.namespace,
                body=updated_deployment,
                dry_run=self.k8s_dry_run,
            )
            print(f"Patched deployment for migration: {self.deployment_name}")
        except ApiException as e:
            print(f"Error patching deployment: {e}")
            sys.exit(1)

    def update_deployment_post_migration(self, migrate_success: bool):
        if migrate_success:
            print("Updating deployment to use database...")
        else:
            print("Resetting deployment to prior state...")

        updated_deployment = copy.deepcopy(self.original_deployment)

        updated_deployment.metadata.resource_version = None

        # if migration is successful, change future connection URI to current connection uri
        if migrate_success and self.dry_run != 'k8s':
            container_env = updated_deployment.spec.template.spec.containers[
                0].env
            db_uri_env_var = {}
            for env_var in container_env:
                if env_var.name == "FUTURE_SKYPILOT_DB_CONNECTION_URI":
                    db_uri_env_var = env_var
                    container_env.remove(env_var)
                    break

            db_uri_env_var.name = "SKYPILOT_DB_CONNECTION_URI"
            container_env.append(db_uri_env_var)

        try:
            self.apps_api.replace_namespaced_deployment(
                name=self.deployment_name,
                namespace=self.namespace,
                body=updated_deployment,
                dry_run=self.k8s_dry_run,
            )
            print(f"Patched deployment to use database: {self.deployment_name}")
        except ApiException as e:
            print(f"Error patching deployment: {e}")
            sys.exit(1)

    def wait_for_migration(self) -> bool:
        print("Waiting for migration to complete...")

        original_resource_version = self.original_deployment.metadata.resource_version
        deployment_label_selector = ','.join([
            f"{k}={v}" for k, v in
            self.original_deployment.spec.selector.match_labels.items()
        ])

        time.sleep(WAIT_TIME)
        wait_start = time.time()

        migration_success = True

        pods = self.core_api.list_namespaced_pod(
            namespace=self.namespace,
            label_selector=deployment_label_selector,
        )

        while not all([
                pod.metadata.resource_version != original_resource_version and
                pod.status.phase == 'Running' for pod in pods.items
        ]):
            print("Waiting for pods to start...")
            time.sleep(WAIT_TIME)

            pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=deployment_label_selector,
            )

            if time.time() - wait_start > 300:
                print("Timeout waiting for pods to start")
                return False

        pod_name = pods.items[0].metadata.name

        lock_appeared = False

        while True:
            # Try to get recent logs
            try:
                logs = self.core_api.read_namespaced_pod_log(
                    name=pod_name,
                    namespace=self.namespace,
                    since_seconds=WAIT_TIME,
                )
                if not logs:
                    logs = " !!!!!! No logs found !!!!! "
                print(logs)

                if 'Starting migration, locking...' in logs:
                    lock_appeared = True

            except ApiException:
                print("Logs not available")

            pods = self.core_api.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=deployment_label_selector,
            )

            new_pods = [
                pod for pod in pods.items
                if pod.metadata.resource_version != original_resource_version
            ]
            if not new_pods:
                print("Waiting for new pods")
                time.sleep(WAIT_TIME)
                continue

            pod_name = new_pods[0].metadata.name

            # check pod status
            pod = self.core_api.read_namespaced_pod(
                name=pod_name,
                namespace=self.namespace,
            )

            if pod.status.phase != 'Running':
                print(f"Pod isnt running: {pod.status}")
                return False

            if pod.status.container_statuses[0].restart_count > 0:
                print(
                    f"Pod restarting: {pod.status.container_statuses[0].restart_count}"
                )
                return False

            if not lock_appeared:
                print(" ~~~~ Waiting for lock to appear...")
                time.sleep(WAIT_TIME)
                continue

            # Check if migration lock file exists
            try:
                exec_response = stream(
                    self.core_api.connect_get_namespaced_pod_exec,
                    name=pod_name,
                    namespace=self.namespace,
                    command=["stat", "/root/sky_db_migration.lock"],
                    stdin=False,
                    stdout=True,
                    stderr=True,
                    tty=False,
                    _preload_content=False,
                )

                exec_out, exec_err = '', ''
                while exec_response.is_open():
                    exec_response.update(timeout=1)
                    if exec_response.peek_stdout():
                        exec_out += exec_response.read_stdout()
                    if exec_response.peek_stderr():
                        exec_err += exec_response.read_stderr()

                exec_response.close()

                if exec_response.returncode == 0:
                    # lock file still exists, migration ongoing
                    # print(" -------- Waiting for migration... -------")
                    time.sleep(WAIT_TIME)
                else:
                    # lock file doesn't exist, migration complete
                    print("Migration complete!")
                    break

            except ApiException:
                print("Error while checking migration status")
                time.sleep(WAIT_TIME)

            except Exception as e:
                print(f"Error while checking migration status: {e}")
                return False

            if time.time() - wait_start > 600:
                print("Timeout waiting for migration")
                return False

        return migration_success

    def backup_sky_config(self):
        print("Backing up sky config...")

        # Get current sky config
        try:
            sky_config = self.core_api.read_namespaced_config_map(
                name=self.config_configmap_name,
                namespace=self.namespace,
            )
        except ApiException as e:
            print(f"Error getting sky config: {e}")
            sys.exit(1)

        backedup_config = sky_config.data.get("config.yaml.bak")
        if backedup_config:
            print("Backup already exists, skipping backup")
            return

        # Create backup configmap
        backup_configmap = client.V1ConfigMap(
            metadata=client.V1ObjectMeta(name=self.config_configmap_name,),
            data={
                "config.yaml": "{}",
                "config.yaml.bak": sky_config.data.get("config.yaml", "{}"),
            })

        try:
            self.core_api.replace_namespaced_config_map(
                name=self.config_configmap_name,
                namespace=self.namespace,
                body=backup_configmap,
                dry_run=self.k8s_dry_run,
            )
        except ApiException as e:
            print(f"Error creating backup configmap: {e}")
            sys.exit(1)

    def launch_migration(self):
        self.start_time = time.time()
        self.start_time_str = datetime.fromtimestamp(
            self.start_time).isoformat()

        print(f"Migration starting at: {self.start_time_str}")

        if self.dry_run != 'none':
            print(f"Running in {self.dry_run} dry-run mode")

        # Step 1: Create volume snapshot
        self.create_volume_snapshot()

        # Step 2: Create migration script configmap
        self.create_migration_configmap()

        # Step 3: Get original deployment
        original_deployment = self.get_deployment()
        self.original_deployment = original_deployment
        original_args = original_deployment.spec.template.spec.containers[
            0].args
        print(f"Original deployment args: {original_args}")

        # Step 4: temporarily delete service
        self.toggle_service()

        # Step 5: Patch deployment to run migration
        self.update_deployment_for_migration()

        # Step 6: Wait for migration to complete (skip in k8s dry-run)
        migration_success = True
        if self.dry_run != 'k8s':
            migration_success = self.wait_for_migration()

        # step 7: update sky config configmap so its not used
        if migration_success and self.dry_run != 'k8s':
            self.backup_sky_config()

        # Step 8: Patch deployment to use database
        self.update_deployment_post_migration(migration_success)

        # Step 9: Restore service configuration
        self.toggle_service()

        # cleanup script configmap
        self.delete_migration_configmap()

        end_time = time.time()
        print(
            f"Migration done at: {datetime.fromtimestamp(end_time).isoformat()} - took {end_time - self.start_time}"
        )

    def cleanup(self):
        if self.cleaned:
            return
        self.cleaned = True
        print("Cleaning up...")

        # Step 1: Delete volume snapshot
        try:
            self._toggle_service_on()
        except:
            pass

        try:
            self.update_deployment_post_migration(False)
        except:
            pass

        try:
            self.delete_migration_configmap()
        except:
            pass

        print("Cleanup done, exiting...")
        sys.exit(1)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migrates a skypilot api server from sqlite to postgres")
    parser.add_argument(
        "-c",
        "--context",
        default=K8S_CONTEXT,
        help=
        "kubernetes context/cluster to migrate api server (default: 'sky-api')")
    parser.add_argument(
        "-n",
        "--namespace",
        default=K8S_NAMESPACE,
        help="Namespace in which the api server is deployed",
    )
    parser.add_argument(
        "-p",
        "--deployment-name",
        default=DEPLOYMENT_NAME,
        help="Name of deployment to edit and migrate",
    )
    parser.add_argument(
        "-s",
        "--service-name",
        default=SERVICE_NAME,
        help="Name of service to delete while migrating",
    )
    parser.add_argument(
        "-m",
        "--script-configmap-name",
        default=SCRIPT_CONFIGMAP_NAME,
        help="Name of configmap to hold migration script",
    )
    parser.add_argument(
        "-f",
        "--config-configmap-name",
        default=CONFIG_CONFIGMAP_NAME,
        help="Name of configmap that has skypilot config",
    )
    parser.add_argument(
        "-sv",
        "--state-volume",
        default=STATE_VOLUME,
        help="Name of skypilot state volume to snapshot",
    )
    parser.add_argument(
        "-sc",
        "--snapshot-class",
        default=SNAPSHOT_CLASS_NAME,
        help="Name of snapshot class to use for volume snapshot")
    parser.add_argument(
        "-d",
        "--dry-run",
        default=DRY_RUN,
        choices=['none', 'k8s', 'db'],
        help=
        "whether to dry run the kubernetes deploy, the db migration, or neither"
    )
    return parser.parse_args()


def main():
    args = parse_args()
    launcher = SkyDbMigrationLauncher(
        context=args.context,
        namespace=args.namespace,
        service_name=args.service_name,
        deployment_name=args.deployment_name,
        state_volume_name=args.state_volume,
        snapshot_class_name=args.snapshot_class,
        script_configmap_name=args.script_configmap_name,
        config_configmap_name=args.config_configmap_name,
        dry_run=args.dry_run,
    )
    signal.signal(signal.SIGINT, lambda sig, frame: launcher.cleanup())

    try:
        launcher.launch_migration()
    except:
        launcher.cleanup()


if __name__ == "__main__":
    main()
