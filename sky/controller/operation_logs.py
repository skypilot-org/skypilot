# https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/dynamodb.html#DynamoDB.Client.create_table
import time
from typing import List

import asyncio
import json
import threading

from concurrent.futures import ThreadPoolExecutor

TABLE_NAME = 'SkyPilotOperationLogs'

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

    def _save_operation_logs(self, operation_id: str,
                             metadata: List[str]) -> dict:
        response = self._client.put_item(
            TableName=TABLE_NAME,
            Item={
                'operation_id': {
                    'S': operation_id
                },
                'metadata': {
                    'S': json.dumps(metadata)
                },
            },
        )
        return response

    def _load_operation_logs(self, operation_id: str) -> dict:
        response = self._client.get_item(
            TableName=TABLE_NAME, Key={'operation_id': {
                'S': operation_id
            }})
        return response.get('Item')

    async def save_operation_logs(self, operation_id: str,
                                  metadata: dict) -> dict:
        future = self._thread_pool.submit(self._save_operation_logs,
                                          operation_id, metadata)
        return await asyncio.wrap_future(future)

    async def load_operation_logs(self, operation_id: str) -> dict:
        future = self._thread_pool.submit(self._load_operation_logs,
                                          operation_id)
        result = await asyncio.wrap_future(future)
        if result is None:
            return {}
        return json.loads(result['metadata']['S'])

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
                'operation_id': item['operation_id']['S'],
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
                'AttributeName': 'operation_id',
                'KeyType': 'HASH'
            },
        ],
        AttributeDefinitions=[
            {
                'AttributeName': 'operation_id',
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


async def save_operation_logs(operation_id: str, metadata: dict) -> dict:
    return await _get_client().save_operation_logs(operation_id, metadata)


async def load_operation_logs(operation_id: str) -> dict:
    return await _get_client().load_operation_logs(operation_id)


async def scan_operation_logs() -> List:
    return await _get_client().scan_clusters()


if __name__ == '__main__':
    # delete_table()
    # create_table()
    print(
        asyncio.run(
            save_operation_logs(
                'op1',
                {
                    'operation': 'start_instances',
                    'user_id': 'test',
                    'successful': True,
                    'timestamp': time.time(),
                },
            )))
    print(asyncio.run(load_operation_logs('op1')))
    # print(asyncio.run(save_operation_logs('Alice', [])))
    print(asyncio.run(scan_operation_logs()))
