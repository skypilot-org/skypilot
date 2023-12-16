import boto3
import json


def create_eventbridge_rule():
    # Specify your AWS region
    region = 'us-east-1'

    # Create a Boto3 EventBridge client
    eventbridge_client = boto3.client('events', region_name=region)

    # Specify the EventBridge rule details
    rule_name = 'SpotInstancePreemptionRule'
    event_pattern = {
        "source": ["aws.ec2"],
        "detail-type": ["EC2 Spot Instance Interruption Warning"],
        "detail": {
            "event-type": ["AWS API Call via CloudTrail"]
        }
    }

    # Create the EventBridge rule
    response = eventbridge_client.put_rule(
        Name=rule_name, EventPattern=json.dumps(event_pattern), State='ENABLED')

    # Print the rule ARN
    print(f"EventBridge rule created with ARN: {response['RuleArn']}")
    rule_arn = response['RuleArn']
    print(response)
    return rule_arn


if __name__ == "__main__":

    rule_arn = create_eventbridge_rule()
    print(rule_arn)
