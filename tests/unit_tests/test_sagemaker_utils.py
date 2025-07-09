from unittest import mock

from sky.utils.aws import sagemaker_utils


def test_launch_training_job(monkeypatch):
    fake_client = mock.MagicMock()
    monkeypatch.setattr(sagemaker_utils.sagemaker_adaptor.boto3, 'client',
                        lambda name: fake_client)
    sagemaker_utils.launch_training_job(
        job_name='test-job',
        image_uri='123.dkr.ecr.us-west-2.amazonaws.com/myimage:latest',
        role='arn:aws:iam::123:role/test')
    fake_client.create_training_job.assert_called_once()
