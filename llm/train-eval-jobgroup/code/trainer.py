#!/usr/bin/env python3
"""Trainer script for ResNet-18 on CIFAR-10.

This script trains a ResNet-18 model on CIFAR-10 and saves checkpoints
periodically to a shared directory that the evaluator can access.

Usage:
    python trainer.py --checkpoint-dir /checkpoints --num-epochs 10 --save-every 2
"""

import argparse
import json
import os
import time

import torch
import torch.nn as nn
import torch.optim as optim
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


def get_dataloaders(batch_size=128):
    """Create training and test dataloaders for CIFAR-10."""
    transform_train = transforms.Compose([
        transforms.RandomCrop(32, padding=4),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465),
                             (0.2023, 0.1994, 0.2010)),
    ])

    transform_test = transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465),
                             (0.2023, 0.1994, 0.2010)),
    ])

    trainset = torchvision.datasets.CIFAR10(root='./data',
                                            train=True,
                                            download=True,
                                            transform=transform_train)
    trainloader = DataLoader(trainset,
                             batch_size=batch_size,
                             shuffle=True,
                             num_workers=2)

    testset = torchvision.datasets.CIFAR10(root='./data',
                                           train=False,
                                           download=True,
                                           transform=transform_test)
    testloader = DataLoader(testset,
                            batch_size=batch_size,
                            shuffle=False,
                            num_workers=2)

    return trainloader, testloader


def save_checkpoint(model, optimizer, epoch, train_loss, checkpoint_dir):
    """Save a training checkpoint."""
    os.makedirs(checkpoint_dir, exist_ok=True)

    checkpoint_path = os.path.join(checkpoint_dir,
                                   f'checkpoint_epoch_{epoch}.pt')
    checkpoint = {
        'epoch': epoch,
        'model_state_dict': model.state_dict(),
        'optimizer_state_dict': optimizer.state_dict(),
        'train_loss': train_loss,
        'timestamp': time.time(),
    }
    torch.save(checkpoint, checkpoint_path)
    print(f"Saved checkpoint: {checkpoint_path}")

    # Update latest.json to point to this checkpoint
    latest_path = os.path.join(checkpoint_dir, 'latest.json')
    with open(latest_path, 'w') as f:
        json.dump(
            {
                'checkpoint': f'checkpoint_epoch_{epoch}.pt',
                'epoch': epoch,
                'train_loss': train_loss,
                'timestamp': time.time(),
            },
            f,
            indent=2)


def train_epoch(model, trainloader, criterion, optimizer, device):
    """Train for one epoch and return average loss."""
    model.train()
    running_loss = 0.0
    total_batches = 0

    for inputs, targets in trainloader:
        inputs, targets = inputs.to(device), targets.to(device)

        optimizer.zero_grad()
        outputs = model(inputs)
        loss = criterion(outputs, targets)
        loss.backward()
        optimizer.step()

        running_loss += loss.item()
        total_batches += 1

    return running_loss / total_batches


def main():
    parser = argparse.ArgumentParser(description='Train ResNet-18 on CIFAR-10')
    parser.add_argument('--checkpoint-dir',
                        type=str,
                        required=True,
                        help='Directory to save checkpoints')
    parser.add_argument('--num-epochs',
                        type=int,
                        default=10,
                        help='Number of training epochs')
    parser.add_argument('--save-every',
                        type=int,
                        default=2,
                        help='Save checkpoint every N epochs')
    parser.add_argument('--batch-size',
                        type=int,
                        default=128,
                        help='Training batch size')
    parser.add_argument('--learning-rate',
                        type=float,
                        default=0.1,
                        help='Initial learning rate')
    args = parser.parse_args()

    # Setup device
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"Using device: {device}")

    # Create model, criterion, optimizer
    model = get_model().to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.SGD(model.parameters(),
                          lr=args.learning_rate,
                          momentum=0.9,
                          weight_decay=5e-4)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer,
                                                     T_max=args.num_epochs)

    # Get data
    print("Loading CIFAR-10 dataset...")
    trainloader, _ = get_dataloaders(args.batch_size)
    print(f"Training samples: {len(trainloader.dataset)}")

    # Training loop
    print("\n" + "=" * 60)
    print("Starting Training")
    print("=" * 60)
    print(f"Epochs: {args.num_epochs}")
    print(f"Batch size: {args.batch_size}")
    print(f"Checkpoint directory: {args.checkpoint_dir}")
    print(f"Saving every {args.save_every} epochs")
    print("=" * 60 + "\n")

    for epoch in range(1, args.num_epochs + 1):
        start_time = time.time()
        train_loss = train_epoch(model, trainloader, criterion, optimizer,
                                 device)
        scheduler.step()
        epoch_time = time.time() - start_time

        print(f"Epoch {epoch}/{args.num_epochs} | "
              f"Loss: {train_loss:.4f} | "
              f"LR: {scheduler.get_last_lr()[0]:.6f} | "
              f"Time: {epoch_time:.1f}s")

        # Save checkpoint
        if epoch % args.save_every == 0 or epoch == args.num_epochs:
            save_checkpoint(model, optimizer, epoch, train_loss,
                            args.checkpoint_dir)

    # Write training complete marker for evaluator
    complete_marker = os.path.join(args.checkpoint_dir, 'training_complete')
    with open(complete_marker, 'w') as f:
        json.dump(
            {
                'final_epoch': args.num_epochs,
                'final_loss': train_loss,
                'timestamp': time.time(),
            },
            f,
            indent=2)
    print(f"Wrote completion marker: {complete_marker}")

    print("\n" + "=" * 60)
    print("Training Complete!")
    print("=" * 60)


if __name__ == '__main__':
    main()
