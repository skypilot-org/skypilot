"""
SAM3 Video Segmentation Script
Processes a video using SAM3 for player and ball segmentation.
"""

import argparse
import gc
import json
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image
from transformers import Sam3VideoModel, Sam3VideoProcessor


# Text prompts for soccer video segmentation
PROMPTS = ["person", "ball"]

# Sample rate for frame extraction (frames per second)
DEFAULT_SAMPLE_FPS = 1

# Maximum frames to process per video (to avoid OOM on long videos)
DEFAULT_MAX_FRAMES = 200


def load_video_frames(
    video_path: str,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> tuple[list[Image.Image], float, float]:
    """Load video frames at a specified sample rate.

    Args:
        video_path: Path to the video file
        sample_fps: Number of frames to extract per second (default: 1)
        max_frames: Maximum number of frames to extract (default: 200)

    Returns:
        Tuple of (frames, original_fps, output_fps)
    """
    cap = cv2.VideoCapture(video_path)
    original_fps = cap.get(cv2.CAP_PROP_FPS)

    # Calculate frame interval for sampling
    if sample_fps <= 0 or sample_fps >= original_fps:
        # Process all frames
        frame_interval = 1
        output_fps = original_fps
    else:
        frame_interval = int(original_fps / sample_fps)
        output_fps = sample_fps

    frames = []
    frame_count = 0

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        # Only keep frames at the sample interval
        if frame_count % frame_interval == 0:
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frames.append(Image.fromarray(frame_rgb))

            # Stop if we've reached max frames
            if max_frames > 0 and len(frames) >= max_frames:
                break

        frame_count += 1

    cap.release()

    return frames, original_fps, output_fps


def overlay_masks_on_frame(
    frame: Image.Image,
    masks: dict[int, np.ndarray],
    colors: dict[int, tuple[int, int, int]],
    alpha: float = 0.5,
) -> Image.Image:
    """Overlay segmentation masks on a video frame."""
    base = np.array(frame).astype(np.float32) / 255.0
    overlay = base.copy()

    for obj_id, mask in masks.items():
        if mask is None:
            continue
        if mask.dtype != np.float32:
            mask = mask.astype(np.float32)
        if mask.ndim == 3:
            mask = mask.squeeze()
        mask = np.clip(mask, 0.0, 1.0)
        color = np.array(colors.get(obj_id, (255, 0, 0)), dtype=np.float32) / 255.0
        m = mask[..., None]
        overlay = (1.0 - alpha * m) * overlay + (alpha * m) * color

    out = np.clip(overlay * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def save_video(frames: list[Image.Image], output_path: str, fps: float = 30.0):
    """Save frames as a video file."""
    if not frames:
        return

    height, width = np.array(frames[0]).shape[:2]
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, fps, (width, height))

    for frame in frames:
        frame_bgr = cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR)
        out.write(frame_bgr)

    out.release()


def process_video(
    model: Sam3VideoModel,
    processor: Sam3VideoProcessor,
    video_path: str,
    output_dir: Path,
    prompts: list[str] = PROMPTS,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    max_frames: int = DEFAULT_MAX_FRAMES,
) -> dict:
    """Process a single video with SAM3 segmentation."""
    video_name = Path(video_path).stem
    print(f"Processing: {video_name}")

    # Load video frames at specified sample rate
    frames, original_fps, output_fps = load_video_frames(
        video_path, sample_fps, max_frames
    )
    if not frames:
        return {"video": video_name, "error": "Could not load video frames"}

    print(f"  Loaded {len(frames)} frames (sampled at {output_fps} fps from {original_fps} fps)")

    # Initialize video session
    inference_session = processor.init_video_session(
        video=frames,
        inference_device="cuda",
        processing_device="cpu",
        video_storage_device="cpu",
        dtype=torch.bfloat16,
    )

    # Add text prompts
    inference_session = processor.add_text_prompt(
        inference_session=inference_session,
        text=prompts,
    )

    # Process all frames
    masks_by_frame = {}
    obj_id_to_prompt = {}

    with torch.no_grad():
        for model_outputs in model.propagate_in_video_iterator(
            inference_session=inference_session,
            max_frame_num_to_track=len(frames),
        ):
            processed_outputs = processor.postprocess_outputs(
                inference_session,
                model_outputs
            )

            frame_idx = model_outputs.frame_idx
            object_ids = processed_outputs["object_ids"]
            masks = processed_outputs["masks"]
            prompt_to_obj_ids = processed_outputs.get("prompt_to_obj_ids", {})

            # Store prompt mapping
            for prompt, obj_ids in prompt_to_obj_ids.items():
                for obj_id in obj_ids:
                    obj_id_to_prompt[int(obj_id)] = prompt

            # Store masks for this frame
            frame_masks = {}
            for i, obj_id in enumerate(object_ids):
                obj_id_int = int(obj_id.item())
                mask_2d = masks[i].float().cpu().numpy()
                if mask_2d.ndim == 3:
                    mask_2d = mask_2d.squeeze()
                mask_2d = (mask_2d > 0.0).astype(np.float32)
                frame_masks[obj_id_int] = mask_2d

            masks_by_frame[frame_idx] = frame_masks

    # Generate colors for each object
    colors = {}
    color_palette = [
        (255, 100, 100),  # Red for person
        (100, 255, 100),  # Green for ball
        (100, 100, 255),  # Blue
        (255, 255, 100),  # Yellow
        (255, 100, 255),  # Magenta
        (100, 255, 255),  # Cyan
    ]
    for obj_id in set(obj_id_to_prompt.keys()):
        prompt = obj_id_to_prompt.get(obj_id, "")
        if "person" in prompt.lower():
            colors[obj_id] = color_palette[0]
        elif "ball" in prompt.lower():
            colors[obj_id] = color_palette[1]
        else:
            colors[obj_id] = color_palette[obj_id % len(color_palette)]

    # Create output frames with overlaid masks
    output_frames = []
    for frame_idx, frame in enumerate(frames):
        masks = masks_by_frame.get(frame_idx, {})
        if masks:
            output_frame = overlay_masks_on_frame(frame, masks, colors)
        else:
            output_frame = frame
        output_frames.append(output_frame)

    # Save output video (write to temp file first, then copy to output dir)
    # This is needed because cv2.VideoWriter doesn't work well with S3 FUSE mounts
    video_output_dir = output_dir / video_name
    video_output_dir.mkdir(parents=True, exist_ok=True)

    output_video_path = video_output_dir / f"{video_name}_segmented.mp4"
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
        tmp_path = tmp_file.name
    try:
        save_video(output_frames, tmp_path, output_fps if output_fps else 1.0)
        shutil.copy2(tmp_path, str(output_video_path))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # Count detections
    total_persons = sum(1 for p in obj_id_to_prompt.values() if "person" in p.lower())
    total_balls = sum(1 for p in obj_id_to_prompt.values() if "ball" in p.lower())

    # Save metadata
    result = {
        "video": video_name,
        "frames_processed": len(frames),
        "original_fps": original_fps,
        "output_fps": output_fps,
        "objects_detected": len(obj_id_to_prompt),
        "persons_detected": total_persons,
        "balls_detected": total_balls,
        "output_video": str(output_video_path),
    }

    metadata_path = video_output_dir / f"{video_name}_metadata.json"
    with open(metadata_path, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"  Detected {total_persons} person(s), {total_balls} ball(s)")
    print(f"  Saved to {output_video_path}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description='Process a video with SAM3 segmentation'
    )
    parser.add_argument(
        'video_path',
        type=str,
        help='Path to the video file to process'
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='/outputs/segmentation_results',
        help='Output directory for segmented videos (default: /outputs/segmentation_results)'
    )
    parser.add_argument(
        '--sample-fps',
        type=float,
        default=DEFAULT_SAMPLE_FPS,
        help=f'Frames per second to sample (default: {DEFAULT_SAMPLE_FPS}). '
             'Use 0 or negative to process all frames.'
    )
    parser.add_argument(
        '--max-frames',
        type=int,
        default=DEFAULT_MAX_FRAMES,
        help=f'Maximum frames per video to avoid OOM (default: {DEFAULT_MAX_FRAMES}). '
             'Use 0 or negative for no limit.'
    )
    args = parser.parse_args()

    video_path = Path(args.video_path)
    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}")
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Video: {video_path}")
    print(f"Output directory: {output_dir}")
    print(f"Sample rate: {args.sample_fps} fps")
    print(f"Max frames: {args.max_frames if args.max_frames > 0 else 'unlimited'}")

    # Load SAM3 model
    print("\nLoading SAM3 model...")
    model = Sam3VideoModel.from_pretrained("facebook/sam3")
    model = model.to("cuda", dtype=torch.bfloat16).eval()
    processor = Sam3VideoProcessor.from_pretrained("facebook/sam3")
    print("Model loaded!")

    try:
        result = process_video(
            model=model,
            processor=processor,
            video_path=str(video_path),
            output_dir=output_dir,
            sample_fps=args.sample_fps,
            max_frames=args.max_frames,
        )
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print("\nProcessing complete!")
        return 0
    except Exception as e:
        print(f"Error processing video: {e}")
        return 1
    finally:
        # Clear GPU memory
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    exit(main())
