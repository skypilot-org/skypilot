"""Delete all images with a given tag and
their associated snapshots from images.csv

Example Usage: put images.csv in the same folder as this script and run
  python image_delete.py  --tag skypilot:custom-gpu-ubuntu-2204
"""

import argparse
import csv
import json
import subprocess

parser = argparse.ArgumentParser(
    description='Delete AWS images and their snapshots across regions.')
parser.add_argument('--tag',
                    required=True,
                    help='Tag of the image to delete, see tags in images.csv')
args = parser.parse_args()


def get_snapshots(image_id, region):
    cmd = (f'aws ec2 describe-images --image-ids {image_id} --region {region}'
           ' --query "Images[*].BlockDeviceMappings[*].Ebs.SnapshotId" '
           '--output json')
    result = subprocess.run(cmd,
                            shell=True,
                            check=True,
                            capture_output=True,
                            text=True)
    snapshots = json.loads(result.stdout)
    return [
        snapshot for sublist in snapshots for snapshot in sublist if snapshot
    ]


def delete_image_and_snapshots(image_id, region):
    # Must get snapshots before deleting the image
    snapshots = get_snapshots(image_id, region)

    # Deregister the image
    cmd = f'aws ec2 deregister-image --image-id {image_id} --region {region}'
    subprocess.run(cmd, shell=True, check=True)
    print(f'Deregistered image {image_id} in region {region}')

    # Delete snapshots
    for snapshot in snapshots:
        cmd = (f'aws ec2 delete-snapshot --snapshot-id {snapshot} '
               f'--region {region}')
        subprocess.run(cmd, shell=True, check=True)
        print(f'Deleted snapshot {snapshot} in region {region}')


def main():
    with open('images.csv', 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row['Tag'] == args.tag:
                try:
                    delete_image_and_snapshots(row['ImageId'], row['Region'])
                except subprocess.CalledProcessError as e:
                    print(f'Failed to delete image {row["ImageId"]} or its '
                          f'snapshots in region {row["Region"]}: {e}')


if __name__ == '__main__':
    main()
