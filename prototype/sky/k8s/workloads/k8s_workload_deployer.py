import logging
from typing import List, Union

import kubernetes
from kubernetes.client import V1Deployment, V1Service

from sky.k8s.k8s_utils import load_k8s_config

logger = logging.getLogger(__name__)


class K8sWorkloadDeployer(object):
    """
    This class executes a list of kubernetes workload objects plan. Objects should be either deployment or service.
    This is a list of V1Deployment and/or V1Service that are required for the application to function.
    """

    def __init__(self, kubeconfig_path):
        load_k8s_config(kubeconfig_path)
        self.coreapi = kubernetes.client.CoreV1Api()
        self.appsapi = kubernetes.client.AppsV1Api()
        super(K8sWorkloadDeployer, self).__init__()

    def deploy_k8s_objects(self,
                           k8s_objects: List[Union[V1Deployment, V1Service]]):
        """
        Given a list of k8s_objects, deploys them onto the running k8s cluster.
        :param k8s_objects: List of V1Deployment or V1Service
        :return:
        """
        for obj in k8s_objects:
            if isinstance(obj, V1Deployment):
                self.appsapi.create_namespaced_deployment(namespace="default",
                                                          body=obj)
            elif isinstance(obj, V1Service):
                self.coreapi.create_namespaced_service(namespace="default",
                                                       body=obj)
            else:
                raise NotImplementedError(f"K8sSe object type {type(obj)} not supported.")
        logger.info(f"Deployed {len(k8s_objects)} kubernetes objects. Check dashboard for status.")
