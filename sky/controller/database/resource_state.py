# https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html#DynamoDB.Client.create_table
import time
from typing import List

import asyncio
import json
import threading

from concurrent.futures import ThreadPoolExecutor

TABLE_NAME = 'SkyPilotResourceState'

_client = None
_boto3_client_lock = threading.Lock()


def _get_client():
    global _client
    with _boto3_client_lock:
        if _client is None:
            _client = AWSDynamoDBClient()
    return _client


class AWSDynamoDBClient:

    def __init__(self):
        import boto3

        self._client = boto3.client('dynamodb', region_name='us-east-1')
        self._thread_pool = ThreadPoolExecutor(32)

    def _save_user_cluster(self, user_id: str, cluster_id: str,
                           metadata: dict) -> dict:
        response = self._client.put_item(
            TableName=TABLE_NAME,
            Item={
                'user_id': {
                    'S': user_id
                },
                'cluster_id': {
                    'S': cluster_id
                },
                'metadata': {
                    'S': json.dumps(metadata)
                }
            },
        )
        return response

    async def save_user_cluster(self, user_id: str, cluster_id: str,
                                metadata: dict) -> dict:
        future = self._thread_pool.submit(self._save_user_cluster, user_id,
                                          cluster_id, metadata)
        return await asyncio.wrap_future(future)

    async def load_user_clusters(self, user_id: str) -> List[dict]:
        raise NotImplementedError

    def _delete_user_cluster(self, user_id: str, cluster_id: str) -> dict:
        response = self._client.delete_item(
            TableName=TABLE_NAME,
            Key={
                'user_id': {
                    'S': user_id
                },
                'cluster_id': {
                    'S': cluster_id
                },
            },
        )
        return response

    async def delete_user_cluster(self, user_id: str, cluster_id: str) -> dict:
        future = self._thread_pool.submit(self._delete_user_cluster, user_id,
                                          cluster_id)
        return await asyncio.wrap_future(future)

    def _scan_clusters(self) -> dict:
        return self._client.scan(TableName=TABLE_NAME).get('Items')

    async def scan_clusters(self) -> List:
        future = self._thread_pool.submit(self._scan_clusters)
        items = await asyncio.wrap_future(future)
        if items is None:
            return []
        results = []
        for item in items:
            results.append({
                'user_id': item['user_id']['S'],
                'cluster_id': item['cluster_id']['S'],
                'metadata': json.loads(item['metadata']['S'])
            })
        return results


def create_table():
    import boto3

    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    table_names = [table.name for table in dynamodb.tables.all()]
    if TABLE_NAME in table_names:
        return

    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {
                'AttributeName': 'user_id',
                'KeyType': 'HASH'
            },
            {
                'AttributeName': 'cluster_id',
                'KeyType': 'RANGE'
            },
        ],
        AttributeDefinitions=[
            {
                'AttributeName': 'user_id',
                'AttributeType': 'S'
            },
            {
                'AttributeName': 'cluster_id',
                'AttributeType': 'S'
            },
        ],
        BillingMode='PAY_PER_REQUEST',
    )

    table.wait_until_exists()


def delete_table():
    import boto3

    dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
    table_names = [table.name for table in dynamodb.tables.all()]
    if TABLE_NAME not in table_names:
        return
    table = dynamodb.Table(TABLE_NAME)
    table.delete()
    table.wait_until_not_exists()


async def save_user_cluster(user_id: str, cluster_id: str,
                            metadata: dict) -> dict:
    return await _get_client().save_user_cluster(user_id, cluster_id, metadata)


async def load_user_clusters(user_id: str) -> List[dict]:
    return await _get_client().load_user_clusters(user_id)


async def delete_user_cluster(user_id: str, cluster_id: str) -> dict:
    return await _get_client().delete_user_cluster(user_id, cluster_id)


async def scan_clusters() -> List:
    return await _get_client().scan_clusters()


if __name__ == '__main__':
    # delete_table()
    # create_table()
    print(
        asyncio.run(
            save_user_cluster('test', 'cluster1', {
                'provider_name': 'AWS',
                'creation_time': time.time() - 3600 * 10,
            })))

    asyncio.run(
        save_user_cluster('test', 'cluster2', {
            'provider_name': 'AZURE',
            'creation_time': time.time() - 3600 * 7,
        }))

    asyncio.run(
        save_user_cluster('test', 'cluster3', {
            'provider_name': 'GCP',
            'creation_time': time.time() - 3600 * 2,
        }))

    asyncio.run(
        save_user_cluster(
            'test', 'cluster4', {
                'provider_name': 'KUBERNETES:my_cluster',
                'creation_time': time.time() - 3600 * 1,
            }))

    asyncio.run(
        save_user_cluster('test', 'cluster5', {
            'provider_name': 'unknown',
            'creation_time': time.time() - 360,
        }))

    asyncio.run(delete_user_cluster('test', 'cluster5'))

    asyncio.run(delete_user_cluster('test1', 'cluster6'))

    # print(asyncio.run(save_user_clusters('Alice', [])))
    print(asyncio.run(scan_clusters()))
