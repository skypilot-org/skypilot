import boto3
from botocore.exceptions import ClientError
from datetime import datetime
import glob
from google.cloud import storage
import googleapiclient.discovery
from googleapiclient import discovery
from oauth2client.client import GoogleCredentials
import multiprocessing
import data_transfer
import os
import json
from typing import Any, Callable, Dict
import sys

InitializeFn = Callable[[str], Any]
Path = str
StorageHandle = str


class StorageBackend(object):
    """
   StorageBackends abstract away the different storage types exposed by
   different clouds. They return a StorageHandle that must be handled by the
   ExecutionBackend to mount onto VMs or containers.
   """

    StorageHandle = str

    def __init__(self, name: str, initialize_fn: InitializeFn):
        self.name = name
        self.initialize_fn = initialize_fn
        self.is_initialized = False
        self.storage_handle = None

    def initialize(self, mount_path: Path) -> StorageHandle:
        """
        Creates the storage backend on the cloud and executes the setup
        function. This might require a VM setup (e.g. for EBS) or directly
        from user's laptop (e.g. s3 cp)
        :param mount_path: Path used for mounting for initialization.
        This path must be used in initialize_fn.
        :return: None
        """
        raise NotImplementedError("Implement in a child class.")

    def cleanup(self):
        """
        Removes the storage object from the cloud
        :return: None
        """
        raise NotImplementedError

    def get_handle(self) -> StorageHandle:
        """
        Returns the storage handle for use by the execution backend to attach
        to VM/containers :return: StorageHandle for the storage backend
        """
        return self.storage_handle


class AWSStorageBackend(StorageBackend):

    def __init__(self, name: str, initialize_fn: InitializeFn):
        region = 'us-east-2'
        self.client = self.create_client(region)
        self.name = name
        self.region = region
        self.bucket = self.get_bucket()

    def cleanup(self):
        return self.delete_s3_bucket(self.name)

    def get_handle(self):
        return boto3.resource('s3').Bucket(self.name)

    def create_client(self, region='us-east-2'):
        return boto3.client('s3', region_name=region)

    def get_bucket(self):
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(self.name)
        if bucket in s3.buckets.all():
            return bucket
        return self.create_s3_bucket(self.name)

    def upload_from_local(self, local_path):
        data_transfer._local_to_s3(local_path, self)

    def download_to_local(self, local_path):
        data_transfer._s3_to_local(self, local_path)

    def create_s3_bucket(self, bucket_name, region='us-east-2'):
        # Create bucket
        s3_client = self.client
        try:
            if region is None:
                response = s3_client.create_bucket(Bucket=bucket_name)
            else:
                location = {'LocationConstraint': region}
                response = s3_client.create_bucket(
                    Bucket=bucket_name, CreateBucketConfiguration=location)
        except ClientError as e:
            print(e)
            return None
        return boto3.resource('s3').Bucket(bucket_name)

    def delete_s3_bucket(self, bucket_name):
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket_name)
        bucket.objects.all().delete()
        bucket.delete()


class GCSStorageBackend(StorageBackend):

    def __init__(self, name: str, initialize_fn: InitializeFn):
        region = 'us-central1'
        self.client = self.create_client(region)
        self.name = name
        self.region = region
        self.bucket = self.get_bucket()

    def cleanup(self):
        return self.delete_gcs_bucket(self.name)

    def get_handle(self):
        return self.client.get_bucket(self.name)

    def create_client(self, region='us-central1'):
        return storage.Client()

    def get_bucket(self):
        try:
            bucket = self.client.get_bucket(self.name)
            return bucket
        except:
            return self.create_gcs_bucket(self.name)

    def upload_from_local(self, local_path):
        data_transfer._local_to_gcs(local_path, self)

    def download_to_local(self, local_path):
        data_transfer._gcs_to_local(self, local_path)

    def create_gcs_bucket(self, bucket_name, region='us-central1'):
        bucket = self.client.bucket(bucket_name)
        bucket.storage_class = "STANDARD"
        new_bucket = self.client.create_bucket(bucket, location=region)
        print("Created bucket {} in {} with storage class {}".format(
            new_bucket.name, new_bucket.location, new_bucket.storage_class))
        return new_bucket

    def delete_gcs_bucket(self, bucket_name):
        bucket = self.client.get_bucket(bucket_name)
        bucket.delete(force=True)


class Storage(object):
    """
    Storage objects handle persistent and large volume storage in the sky.
    Users create Storage objects with an initialize_fn and a default mount poth.
    Power users can specify their pre-initialized backends if their data is
    already on the cloud.
    """

    def __init__(self,
                 name: str,
                 initialize_fn: InitializeFn,
                 default_mount_path: Path,
                 storage_backends: Dict[str, StorageBackend] = None,
                 persistent: bool = False):
        """
        :param name: Name of the storage object. Used as the unique id for
        persistence.
        :param initialize_fn: Shell commands to run to initialize storage.
        All paths must be absolute (using the default_mount_path)
        :param default_mount_path: Default path to mount this storage at.
        :param storage_backends: Optional - specify  pre-initialized
        storage backends
        :param persistent: Whether to persist across sky runs.
        """
        self.name = name
        self.initialize_fn = initialize_fn
        self.default_mount_path = default_mount_path
        self.persistent = persistent

        # Sky optimizer either adds a storage backend instance or selects
        # from existing ones
        if storage_backends is None:
            self.storage_backends = {}
        else:
            self.storage_backends = storage_backends

        super(Storage, self).__init__()

    def add_backend(self, backend: StorageBackend) -> None:
        """
        Invoked by the optimizer after it has created a storage backend to
        add it to Storage.
        """
        assert backend.is_initialized
        backend_key = type(backend)
        assert backend_key not in self.storage_backends, f"Storage type {backend_key} already exists, why do " \
                                                         f"you want to add another of the same type? "
        self.storage_backends[backend_key] = backend

    def cleanup(self):
        """
        If not persistent, deletes data from all storage backends.
        :return:
        """
        if not self.persistent:
            for _, backend in self.storage_backends.items():
                backend.cleanup()


#aws_backend = AWSStorageBackend("imagenet-dataset-1", None)
#gcp_backend = GCSStorageBackend("christmasbalsa", None)
#aws_backend.download_to_local("~/Downloads/temp/")
#_s3_to_gcs(aws_backend, gcp_backend)
#aws_backend.upload_from_local("~/Downloads/tpu/")
#_s3_to_gcs(aws_backend, gcp_backend)
#aws_backend.cleanup()
