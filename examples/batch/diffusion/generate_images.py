"""Batch image generation with Stable Diffusion on Sky Batch.

This example generates images from text prompts using Stable Diffusion,
distributed across a pool of GPU workers.

Input:  JSONL file with "prompt" fields
Output: PNG images + manifest.jsonl in a cloud directory

Usage (from project root):
    1. Prepare bucket and prompts:
       $ bash examples/batch/diffusion/prepare.sh

    2. Run (uses bucket from prepare.sh, or a default based on $USER):
       $ export SKY_BATCH_BUCKET=<bucket printed by prepare.sh>  # optional
       $ python examples/batch/diffusion/generate_images.py

    3. Check output:
       $ aws s3 cp s3://$SKY_BATCH_BUCKET/generated_images/ ./generated_images/ --recursive
"""
import os
import tempfile

import sky
from sky.batch import utils
from sky.serve import serve_utils


@sky.batch.remote_function
def generate_images():
    """Generate images from text prompts using Stable Diffusion.

    The model is loaded once per worker and reused across all batches.
    Each prompt produces one PNG image.
    """
    import torch
    from diffusers import StableDiffusionPipeline

    # Load model once (amortized across all batches on this worker).
    # Using SD v1.5 (public, no HuggingFace auth required).
    pipe = StableDiffusionPipeline.from_pretrained(
        'stable-diffusion-v1-5/stable-diffusion-v1-5',
        torch_dtype=torch.float16,
    )
    pipe = pipe.to('cuda')

    for batch in sky.batch.load():
        prompts = [item['prompt'] for item in batch]
        result = pipe(prompts)

        # Each result dict must contain an 'image' key with a PIL Image.
        # The framework saves each image as a PNG file and records the
        # filename in manifest.jsonl.
        results = []
        for item, img in zip(batch, result.images):
            results.append({
                'prompt': item['prompt'],
                'image': img,
            })

        sky.batch.save_results(results)


def ensure_pool(pool_name: str, pool_yaml: str) -> None:
    """Create the pool if it doesn't already exist."""
    request_id = sky.jobs.pool_status([pool_name])
    pool_statuses = sky.stream_and_get(request_id)
    if pool_statuses:
        print(f'Pool {pool_name} already exists, skipping pool apply.')
        return

    print(f'Pool {pool_name} not found. Creating from {pool_yaml}...')
    task = sky.Task.from_yaml(pool_yaml)
    task.name = pool_name
    request_id = sky.jobs.pool_apply(
        task,
        pool_name=pool_name,
        mode=serve_utils.DEFAULT_UPDATE_MODE,
    )
    sky.stream_and_get(request_id)
    print(f'Pool {pool_name} is ready.')


def main():
    # Configuration — set SKY_BATCH_BUCKET via prepare.sh, or falls back
    # to a default bucket name derived from the OS username.
    default_bucket = f'sky-batch-diffusion-{os.environ.get("USER", "default")}'
    bucket = os.environ.get('SKY_BATCH_BUCKET', default_bucket)
    print(f'Using bucket: s3://{bucket}  '
          f'(override with SKY_BATCH_BUCKET env var)')

    input_path = f's3://{bucket}/prompts.jsonl'
    output_path = f's3://{bucket}/generated_images/'
    pool_name = 'diffusion-pool'
    pool_yaml = os.path.join(os.path.dirname(__file__), 'pool.yaml')

    # Create dataset from cloud storage
    ds = sky.dataset(input_path)

    # Ensure the pool exists (creates it if needed)
    ensure_pool(pool_name, pool_yaml)

    # Process the dataset.
    # The trailing '/' in output_path tells the framework to use
    # image directory output: each image is saved as a separate PNG,
    # and a manifest.jsonl maps prompts to filenames.
    print(f'Generating images from {input_path}...')
    ds.map(
        generate_images,
        pool_name=pool_name,
        batch_size=3,
        output_path=output_path,
        # Must match the venv created in pool.yaml setup.
        activate_env='source .venv/bin/activate',
    )

    print(f'\nDone! Images saved to {output_path}')
    print(f'Manifest: {output_path}manifest.jsonl')

    # Download images and display in terminal
    print('\n' + '=' * 60)
    print('RESULTS:')
    print('=' * 60)
    try:
        manifest = utils.load_jsonl_from_cloud(
            f'{output_path}manifest.jsonl')

        # Download images locally
        tmpdir = tempfile.mkdtemp(prefix='sky_batch_diffusion_')
        for i, entry in enumerate(manifest, 1):
            image_url = f'{output_path}{entry["image"]}'
            local_path = os.path.join(tmpdir, entry['image'])
            utils.download_file_from_cloud(image_url, local_path)
            print(f'  {i}. {entry["prompt"]}  ->  {local_path}')

        print('=' * 60)
        print(f'Total images: {len(manifest)}')
        print(f'Downloaded to: {tmpdir}')
    except Exception as e:
        print(f'Error loading results: {e}')
        print(f'You can manually check: {output_path}')


if __name__ == '__main__':
    main()
