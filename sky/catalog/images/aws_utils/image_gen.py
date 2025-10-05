"""Copy SkyPilot AMI to multi regions, make them public, and generate images.csv

Example Usage:
  python aws_image_gen.py  --source-image-id ami-00000 --processor gpu
"""

import argparse
import concurrent.futures
import csv
import json
import os
import subprocess
import time

parser = argparse.ArgumentParser(
    description='Generate AWS images across regions.')
parser.add_argument('--image-id',
                    required=True,
                    help='The source AMI ID to copy from')
parser.add_argument('--processor', required=True, help='e.g. gpu, cpu, etc.')
parser.add_argument('--region',
                    default='us-east-1',
                    help='Region of the source AMI')
parser.add_argument('--base-image-id',
                    default='ami-005fc0f236362e99f',
                    help='The base AMI of the source AMI.')
parser.add_argument('--os-type', default='ubuntu', help='The OS type')
parser.add_argument('--os-version', default='22.04', help='The OS version')
parser.add_argument('--arch', default='x86_64', help='The architecture')
parser.add_argument('--output-csv',
                    default='images.csv',
                    help='The output CSV file name')
args = parser.parse_args()

# 25 regions
ALL_REGIONS = [
    # 'us-east-1',  # Source AMI is already in this region
    'us-east-2',
    'us-west-1',
    'us-west-2',
    'ca-central-1',
    'eu-central-1',  # need for smoke test
    'eu-central-2',
    'eu-west-1',
    'eu-west-2',
    'eu-south-1',
    'eu-south-2',
    'eu-west-3',
    'eu-north-1',
    'me-south-1',
    'me-central-1',
    'af-south-1',
    'ap-east-1',
    'ap-south-1',
    'ap-south-2',
    'ap-northeast-3',
    'ap-northeast-2',
    'ap-southeast-1',
    'ap-southeast-2',
    'ap-southeast-3',
    'ap-northeast-1',
]


def make_image_public(image_id, region):
    unblock_command = (
        f'aws ec2 disable-image-block-public-access --region {region}')
    subprocess.run(unblock_command, shell=True, check=True)
    public_command = (
        f'aws ec2 modify-image-attribute --image-id {image_id} '
        '--launch-permission "{\\\"Add\\\": [{\\\"Group\\\":\\\"all\\\"}]}" '
        f'--region {region}')
    subprocess.run(public_command, shell=True, check=True)
    print(f'Made {image_id} public')


def copy_image_and_make_public(target_region):
    # Copy the AMI to the target region
    if args.arch == 'arm64':
        ami_name = (f'skypilot-aws-{args.processor}-{args.os_type}-{args.arch}-'
                    f'{time.strftime("%y%m%d")}')
    else:
        ami_name = (f'skypilot-aws-{args.processor}-{args.os_type}-'
                    f'{time.strftime("%y%m%d")}')
    copy_command = (
        f'aws ec2 copy-image --source-region {args.region} '
        f'--source-image-id {args.image_id} --region {target_region} '
        f'--name "{ami_name}"  '
        '--output json')
    print(copy_command)
    result = subprocess.run(copy_command,
                            shell=True,
                            check=True,
                            capture_output=True,
                            text=True)
    print(result.stdout)
    new_image_id = json.loads(result.stdout)['ImageId']
    print(f'Copied image to {target_region} with new image ID: {new_image_id}')

    # Wait for the image to be available
    print(f'Waiting for {new_image_id} to be available...')
    wait_command = (f'aws ec2 wait image-available --image-ids {new_image_id} '
                    f'--region {target_region}')
    subprocess.run(wait_command, shell=True, check=True)

    make_image_public(new_image_id, target_region)

    return new_image_id


def write_image_to_csv(image_id, region):
    with open(args.output_csv, 'a', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        if args.arch == 'arm64':
            tag = f'skypilot:custom-{args.processor}-{args.os_type}-{args.arch}'
        else:
            tag = f'skypilot:custom-{args.processor}-{args.os_type}'
        row = [
            tag, region, args.os_type, args.os_version, image_id,
            time.strftime('%Y%m%d'), args.base_image_id
        ]
        writer.writerow(row)
    print(f'Wrote to CSV: {row}')


def main():
    make_image_public(args.image_id, args.region)
    if not os.path.exists(args.output_csv):
        with open(args.output_csv, 'w', newline='',
                  encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'Tag', 'Region', 'OS', 'OSVersion', 'ImageId', 'CreationDate',
                'BaseImageId'
            ])  # Header
        print(f'No existing {args.output_csv} so created it.')

    # Process other regions
    image_cache = [(args.image_id, args.region)]

    def process_region(copy_to_region):
        print(f'Start copying image to {copy_to_region}...')
        try:
            new_image_id = copy_image_and_make_public(copy_to_region)
        except Exception as e:  # pylint: disable=broad-except
            print(f'Error generating image to {copy_to_region}: {str(e)}')
            new_image_id = 'NEED_FALLBACK'
        image_cache.append((new_image_id, copy_to_region))

    with concurrent.futures.ThreadPoolExecutor() as executor:
        executor.map(process_region, ALL_REGIONS)
    executor.shutdown(wait=True)

    # Sort the images by it's region and write to CSV
    sorted_image_cache = sorted(image_cache, key=lambda x: x[1])
    for new_image_id, copy_to_region in sorted_image_cache:
        write_image_to_csv(new_image_id, copy_to_region)

    print('All done!')


if __name__ == '__main__':
    main()
