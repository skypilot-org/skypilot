"""
Run `python aws_image_gen.py` to copy the AMI in the source region to all regions and make them public.
Will also generate a `images.csv` or append to an existing one; then we can refresh the `images.csv` inSkyPilot Catalog.
"""

import csv
import json
import os
import subprocess
import time

# Inputs based on Packer generated AMI
IMAGE_REGION = 'us-east-1'
IMAGE_ID = 'ami-0981cc842c7188227'
BASE_IMAGE_ID = 'ami-005fc0f236362e99f'
OS_TYPE = 'ubuntu'
OS_VERSION = '22.04'

OUTPUT_CSV = 'images.csv'
ALL_REGIONS = [
    'us-west-1',
]


def copy_image_and_make_public(source_region, source_image_id, target_region):
    # Copy the AMI to the target region
    copy_command = f"aws ec2 copy-image --source-region {source_region} --source-image-id {source_image_id} --region {target_region} --name 'skypilot-aws-gpu-ubuntu-{time.time()}'"
    result = subprocess.run(copy_command,
                            shell=True,
                            check=True,
                            capture_output=True,
                            text=True)
    new_image_id = json.loads(result.stdout)['ImageId']
    print(f"Copied image to {target_region}: {new_image_id}")

    # Wait for the image to be available
    print(f"Waiting for {new_image_id} to be available...")
    wait_command = f"aws ec2 wait image-available --image-ids {new_image_id} --region {target_region}"
    subprocess.run(wait_command, shell=True, check=True)

    # Make the image public
    unblock_command = f"aws ec2 disable-image-block-public-access --region {target_region}"
    subprocess.run(unblock_command, shell=True, check=True)
    public_command = f'aws ec2 modify-image-attribute --image-id {new_image_id} --launch-permission "{{\\\"Add\\\": [{{\\\"Group\\\":\\\"all\\\"}}]}}" --region {target_region}'
    subprocess.run(public_command, shell=True, check=True)
    print(f"Made {new_image_id} public")

    return new_image_id


def write_image_to_csv(image_id, region, os_type, os_version):
    with open(OUTPUT_CSV, 'a', newline='') as csvfile:
        writer = csv.writer(csvfile)
        row = [
            f'skypilot:gpu-{os_type}-{os_version.replace(".", "")}', region,
            os_type, os_version, image_id,
            time.strftime('%Y%m%d'), BASE_IMAGE_ID
        ]
        writer.writerow(row)
    print(f"Wrote to CSV: {row}")


def main():
    if not os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow([
                'Tag', 'Region', 'OS', 'OSVersion', 'ImageId', 'CreationDate',
                'BaseImageId'
            ])  # Header
        print(f"Created empty {OUTPUT_CSV}")

    for region in ALL_REGIONS:
        new_image_id = copy_image_and_make_public(IMAGE_REGION, IMAGE_ID,
                                                  region)
        write_image_to_csv(new_image_id, region, OS_TYPE, OS_VERSION)

    print("All done!")


if __name__ == "__main__":
    main()
