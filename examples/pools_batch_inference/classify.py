#!/usr/bin/env python3
"""Batch text classification script using vLLM for sentiment analysis.

This script processes a partition of the IMDB dataset based on job rank.
"""

import argparse
import json
from pathlib import Path
import sys
import time

from datasets import load_dataset
from tqdm import tqdm
from vllm import LLM
from vllm import SamplingParams

CLASSIFICATION_PROMPT = (
    'You are a sentiment classifier. Classify the following movie review '
    'as either "positive", "negative", or "neutral". '
    'Output ONLY the label, nothing else.\n\n'
    'Review: {text}\n'
    'Classification:')


def calculate_partition(total_items: int, job_rank: int,
                        num_jobs: int) -> tuple:
    """Calculate the start and end indices for this job's partition."""
    items_per_job = total_items // num_jobs
    remainder = total_items % num_jobs

    # Distribute remainder across first few jobs
    start_idx = job_rank * items_per_job + min(job_rank, remainder)
    end_idx = start_idx + items_per_job + (1 if job_rank < remainder else 0)

    return start_idx, end_idx


def parse_classification(text: str) -> str:
    """Parse the classification result from the model output.

    Args:
        text: Raw text output from the model

    Returns:
        Classification label (positive/negative/neutral)
    """
    text = text.strip().lower()

    # Normalize the result
    if 'positive' in text:
        return 'positive'
    elif 'negative' in text:
        return 'negative'
    elif 'neutral' in text:
        return 'neutral'
    else:
        # Default to the raw result if it doesn't match expected labels
        return text[:20]  # Truncate to avoid long error messages


def main():
    parser = argparse.ArgumentParser(
        description='Batch text classification with vLLM')
    parser.add_argument('--job-rank',
                        type=int,
                        required=True,
                        help='Rank of this job (0-indexed)')
    parser.add_argument('--num-jobs',
                        type=int,
                        required=True,
                        help='Total number of jobs')
    parser.add_argument('--output-dir',
                        type=str,
                        required=True,
                        help='Directory to save results')
    parser.add_argument('--model-path',
                        type=str,
                        required=True,
                        help='Path to the vLLM model')
    parser.add_argument(
        '--dataset-size',
        type=int,
        default=5000,
        help='Total number of reviews to process across all jobs')
    parser.add_argument('--batch-size',
                        type=int,
                        default=32,
                        help='Number of texts to process in each batch')

    args = parser.parse_args()

    print('=' * 80)
    print(
        f'Batch Text Classification - Job Rank {args.job_rank}/{args.num_jobs}')
    print('=' * 80)
    print(f'Model Path: {args.model_path}')
    print(f'Output Directory: {args.output_dir}')
    print(f'Batch Size: {args.batch_size}')
    print()

    # Load IMDB dataset
    print('Loading IMDB dataset...')
    try:
        dataset = load_dataset('imdb', split='test')
        print(f'✓ Loaded {len(dataset)} reviews from IMDB dataset')
    except Exception as e:  # pylint: disable=broad-except
        print(f'✗ Failed to load dataset: {e}')
        sys.exit(1)

    # Calculate partition for this job
    total_items = len(dataset)
    start_idx, end_idx = calculate_partition(total_items, args.job_rank,
                                             args.num_jobs)
    partition_size = end_idx - start_idx

    print(f'Total Dataset Size: {total_items}')

    print(f'\nProcessing partition: {start_idx} to {end_idx} '
          f'({partition_size} reviews)')
    print()

    # Initialize vLLM
    print('Initializing vLLM...')
    try:
        sampling_params = SamplingParams(
            temperature=0.0,  # Deterministic for classification
            top_p=1.0,
            max_tokens=10,
        )

        llm = LLM(
            model=args.model_path,
            dtype='auto',
            max_model_len=2048,
        )
        print('✓ vLLM initialized successfully')
    except Exception as e:  # pylint: disable=broad-except
        print(f'✗ Failed to initialize vLLM: {e}')
        sys.exit(1)

    print()

    # Prepare all reviews and prompts for this partition
    print('Preparing prompts...')
    reviews = []
    true_labels = []
    prompts = []

    for idx in range(start_idx, end_idx):
        review = dataset[idx]
        text = review['text']
        true_label = 'positive' if review['label'] == 1 else 'negative'

        reviews.append({
            'index': idx,
            'text': text,
            'true_label': true_label,
        })
        true_labels.append(true_label)

        # Truncate review text to avoid very long prompts
        truncated_text = text[:1000] if len(text) > 1000 else text
        prompt = CLASSIFICATION_PROMPT.format(text=truncated_text)
        prompts.append(prompt)

    print(f'✓ Prepared {len(prompts)} prompts')
    print()

    # Process in batches for better efficiency
    print('Running batch inference...')
    start_time = time.time()

    all_results = []

    # Process all prompts at once (vLLM handles batching internally)
    try:
        outputs = llm.generate(prompts, sampling_params)

        # Parse results
        for i, output in enumerate(tqdm(outputs, desc=f'Job {args.job_rank}')):
            generated_text = output.outputs[0].text
            predicted_label = parse_classification(generated_text)

            review_text = reviews[i]['text']
            truncated_review = (review_text[:200] + '...'
                                if len(review_text) > 200 else review_text)
            result = {
                'index': reviews[i]['index'],
                'text': truncated_review,
                'true_label': reviews[i]['true_label'],
                'predicted_label': predicted_label,
                'correct': predicted_label == reviews[i]['true_label'],
            }
            all_results.append(result)

    except Exception as e:  # pylint: disable=broad-except
        print(f'✗ Error during inference: {e}')
        sys.exit(1)

    elapsed_time = time.time() - start_time

    # Calculate statistics
    correct_predictions = sum(1 for r in all_results if r['correct'])
    accuracy = (correct_predictions / len(all_results) *
                100) if all_results else 0
    throughput = len(all_results) / elapsed_time if elapsed_time > 0 else 0

    print()
    print('=' * 80)
    print(f'Job {args.job_rank} Complete!')
    print('=' * 80)
    print(f'Processed: {len(all_results)} reviews')
    print(
        f'Accuracy: {accuracy:.1f}% ({correct_predictions}/{len(all_results)})')
    print(f'Time: {elapsed_time:.1f}s')
    print(f'Throughput: {throughput:.2f} reviews/sec')
    print()

    # Save results
    output_path = Path(args.output_dir) / f'results_rank_{args.job_rank}.jsonl'
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        for result in all_results:
            f.write(json.dumps(result) + '\n')

    # Also save summary statistics
    summary_path = Path(args.output_dir) / f'summary_rank_{args.job_rank}.json'
    summary = {
        'job_rank': args.job_rank,
        'num_jobs': args.num_jobs,
        'start_idx': start_idx,
        'end_idx': end_idx,
        'total_processed': len(all_results),
        'correct_predictions': correct_predictions,
        'accuracy': accuracy,
        'elapsed_time_seconds': elapsed_time,
        'throughput_reviews_per_sec': throughput,
    }

    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, indent=2)

    print(f'✓ Results saved to {output_path}')
    print(f'✓ Summary saved to {summary_path}')
    print()


if __name__ == '__main__':
    main()
