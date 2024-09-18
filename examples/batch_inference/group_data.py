import argparse
import os
import math

def group_data(data_paths: str, num_groups: int):
    # Chunk data paths in to multiple groups
    data_groups = []
    group_size = math.ceil(len(data_paths) / num_groups)
    for i in range(num_groups):
        data_groups.append(data_paths[i * group_size:(i + 1) * group_size])
    return data_groups

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-metadata', type=str, required=True, help='The file that contains all paths to data to be chunked.')
    parser.add_argument('--num-groups', type=int, required=True, help='The number of groups to be created.')
    args = parser.parse_args()

    with open(args.data_metadata, 'r') as f:
        data_paths = f.readlines()
        data_paths = [path.strip() for path in data_paths]

    data_groups = group_data(data_paths, args.num_groups)

    # Save data groups to different files
    os.makedirs('./groups', exist_ok=True)
    for i, data_group in enumerate(data_groups):
        with open(f'./groups/{i}.txt', 'w') as f:
            f.write('\n'.join(data_group))
