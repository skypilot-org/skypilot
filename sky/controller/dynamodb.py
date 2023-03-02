# https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html#DynamoDB.Client.create_table
import time
from typing import List

import asyncio
import json
import threading

from concurrent.futures import ThreadPoolExecutor

TABLE_NAME = "SkyPilotUsers"

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

        self._client = boto3.client("dynamodb", region_name="us-east-1")
        self._thread_pool = ThreadPoolExecutor(32)

    def _save_user_clusters(self, user_id: str, clusters: List[str]) -> dict:
        response = self._client.put_item(
            TableName=TABLE_NAME,
            Item={
                "user_id": {
                    "S": user_id
                },
                "clusters": {
                    "S": json.dumps(clusters)
                },
            },
        )
        return response

    def _load_user_clusters(self, user_id: str) -> dict:
        response = self._client.get_item(TableName=TABLE_NAME,
                                         Key={"user_id": {
                                             "S": user_id
                                         }})
        return response.get("Item")

    async def save_user_clusters(self, user_id: str,
                                 clusters: List[dict]) -> dict:
        future = self._thread_pool.submit(self._save_user_clusters, user_id,
                                          clusters)
        return await asyncio.wrap_future(future)

    async def load_user_clusters(self, user_id: str) -> List[dict]:
        future = self._thread_pool.submit(self._load_user_clusters, user_id)
        result = await asyncio.wrap_future(future)
        if result is None:
            return []
        return json.loads(result['clusters']['S'])

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
                'clusters': json.loads(item['clusters']['S'])
            })
        return results


def create_table():
    import boto3

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table_names = [table.name for table in dynamodb.tables.all()]
    if TABLE_NAME in table_names:
        return

    table = dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {
                "AttributeName": "user_id",
                "KeyType": "HASH"
            },
        ],
        AttributeDefinitions=[
            {
                "AttributeName": "user_id",
                "AttributeType": "S"
            },
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    table.wait_until_exists()


def delete_table():
    import boto3

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table_names = [table.name for table in dynamodb.tables.all()]
    if TABLE_NAME not in table_names:
        return
    table = dynamodb.Table(TABLE_NAME)
    table.delete()
    table.wait_until_not_exists()


async def save_user_clusters(user_id: str, clusters: List[dict]) -> dict:
    return await _get_client().save_user_clusters(user_id, clusters)


async def load_user_clusters(user_id: str) -> List[dict]:
    return await _get_client().load_user_clusters(user_id)


async def scan_clusters() -> List:
    return await _get_client().scan_clusters()


if __name__ == '__main__':
    # delete_table()
    # create_table()
    print(
        asyncio.run(
            save_user_clusters('test', [
                {
                    'cluster_name': 'cluster1',
                    'provider_name': 'AWS',
                    'creation_time': time.time() - 3600 * 10,
                },
                {
                    'cluster_name': 'cluster2',
                    'provider_name': 'AZURE',
                    'creation_time': time.time() - 3600 * 7,
                },
                {
                    'cluster_name': 'cluster3',
                    'provider_name': 'GCP',
                    'creation_time': time.time() - 3600 * 2,
                },
                {
                    'cluster_name': 'cluster4',
                    'provider_name': 'KUBERNETES:my_cluster',
                    'creation_time': time.time() - 3600 * 1,
                },
            ])))
    print(asyncio.run(load_user_clusters('test')))
    # print(asyncio.run(save_user_clusters('Alice', [])))
    print(asyncio.run(scan_clusters()))
