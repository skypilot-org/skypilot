#!/usr/bin/env python3
"""Evaluator script that watches for new checkpoints and evaluates them.

This script monitors a checkpoint directory and evaluates new checkpoints
as they appear, reporting accuracy on the CIFAR-10 test set.

Usage:
    python evaluator.py --checkpoint-dir /checkpoints
"""

import argparse
import glob
import os
import time

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
import torchvision
import torchvision.transforms as transforms


def get_model():
    """Create a ResNet-18 model for CIFAR-10."""
    model = torchvision.models.resnet18(weights=None)
    # Modify for CIFAR-10 (32x32 images, 10 classes)
    model.conv1 = nn.Conv2d(3,
                            64,
                            kernel_size=3,
                            stride=1,
                            padding=1,
                            bias=False)
    model.maxpool = nn.Identity()
    model.fc = nn.Linear(model.fc.in_features, 10)
    return model


def get_test_dataloader(batch_size=128):
    """Create test dataloader for CIFAR-10."""
    transform_test = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465),
                             (0.2023, 0.1994, 0.2010)),
    ])

    testset = torchvision.datasets.CIFAR10(root='./data',
                                           train=False,
                                           download=True,
                                           transform=transform_test)
    testloader = DataLoader(testset,
                            batch_size=batch_size,
                            shuffle=False,
                            num_workers=2)

    return testloader


def evaluate(model, testloader, device):
    """Evaluate model on test set and return accuracy."""
    model.eval()
    correct = 0
    total = 0

    with torch.no_grad():
        for inputs, targets in testloader:
            inputs, targets = inputs.to(device), targets.to(device)
            outputs = model(inputs)
            _, predicted = outputs.max(1)
            total += targets.size(0)
            correct += predicted.eq(targets).sum().item()

    accuracy = 100.0 * correct / total
    return accuracy


def get_checkpoint_files(checkpoint_dir):
    """Get list of checkpoint files in directory."""
    pattern = os.path.join(checkpoint_dir, 'checkpoint_epoch_*.pt')
    return set(glob.glob(pattern))


def load_checkpoint(checkpoint_path, model, device):
    """Load checkpoint and return metadata."""
    checkpoint = torch.load(checkpoint_path,
                            map_location=device,
                            weights_only=False)
    model.load_state_dict(checkpoint['model_state_dict'])
    return {
        'epoch': checkpoint['epoch'],
        'train_loss': checkpoint['train_loss'],
        'timestamp': checkpoint.get('timestamp', 0),
    }


def main():
    parser = argparse.ArgumentParser(
        description='Evaluate checkpoints as they appear')
    parser.add_argument('--checkpoint-dir',
                        type=str,
                        required=True,
                        help='Directory to watch for checkpoints')
    parser.add_argument('--poll-interval',
                        type=int,
                        default=5,
                        help='Seconds between polling for new checkpoints')
    parser.add_argument('--batch-size',
                        type=int,
                        default=128,
                        help='Evaluation batch size')
    args = parser.parse_args()

    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")

    # Create model
    model = get_model().to(device)

    # Get test data
    print("Loading CIFAR-10 test dataset...")
    testloader = get_test_dataloader(args.batch_size)
    print(f"Test samples: {len(testloader.dataset)}")

    print("\n" + "=" * 60)
    print("Checkpoint Evaluator")
    print("=" * 60)
    print(f"Watching directory: {args.checkpoint_dir}")
    print(f"Poll interval: {args.poll_interval} seconds")
    print("=" * 60 + "\n")

    # Track evaluated checkpoints
    evaluated_checkpoints = set()
    results = []

    print("Waiting for checkpoints...")
    print("-" * 60)

    training_complete = False
    complete_marker_path = os.path.join(args.checkpoint_dir,
                                        'training_complete')

    while True:
        # Get current checkpoint files
        current_checkpoints = get_checkpoint_files(args.checkpoint_dir)

        # Find new checkpoints
        new_checkpoints = current_checkpoints - evaluated_checkpoints

        if new_checkpoints:
            # Sort by epoch number
            sorted_checkpoints = sorted(
                new_checkpoints,
                key=lambda x: int(
                    os.path.basename(x).split('_')[-1].replace('.pt', '')))

            for checkpoint_path in sorted_checkpoints:
                try:
                    # Load and evaluate
                    metadata = load_checkpoint(checkpoint_path, model, device)
                    accuracy = evaluate(model, testloader, device)

                    result = {
                        'checkpoint': os.path.basename(checkpoint_path),
                        'epoch': metadata['epoch'],
                        'train_loss': metadata['train_loss'],
                        'test_accuracy': accuracy,
                    }
                    results.append(result)

                    print(f"Epoch {metadata['epoch']:3d} | "
                          f"Train Loss: {metadata['train_loss']:.4f} | "
                          f"Test Accuracy: {accuracy:.2f}%")

                    evaluated_checkpoints.add(checkpoint_path)

                except Exception as e:
                    print(f"Error evaluating {checkpoint_path}: {e}")
                    # Don't mark as evaluated, will retry next poll
                    continue

        # Check if training is complete (look for training_complete marker)
        if os.path.exists(complete_marker_path):
            if not training_complete:
                print("\nDetected training completion marker.")
                training_complete = True

            # Evaluate any remaining checkpoints
            remaining = get_checkpoint_files(
                args.checkpoint_dir) - evaluated_checkpoints
            if not remaining:
                print("All checkpoints evaluated. Exiting.")
                break

        time.sleep(args.poll_interval)

    # Final summary
    print("\n" + "=" * 60)
    print("Evaluation Complete!")
    print("=" * 60)

    if results:
        print("\nResults Summary:")
        print("-" * 60)
        print(f"{'Epoch':>6} | {'Train Loss':>12} | {'Test Accuracy':>14}")
        print("-" * 60)
        for r in results:
            print(f"{r['epoch']:>6} | {r['train_loss']:>12.4f} | "
                  f"{r['test_accuracy']:>13.2f}%")
        print("-" * 60)

        best = max(results, key=lambda x: x['test_accuracy'])
        print(f"\nBest: Epoch {best['epoch']} with "
              f"{best['test_accuracy']:.2f}% accuracy")

    print("=" * 60)


if __name__ == '__main__':
    main()
