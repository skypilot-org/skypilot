"""SAM3 video segmentation for soccer players and ball."""

import argparse
import gc
import json
from pathlib import Path
import shutil
import tempfile

import cv2
import numpy as np
from PIL import Image
import torch
from transformers import Sam3VideoModel
from transformers import Sam3VideoProcessor

PROMPTS = ["soccer player", "ball"]
PLAYER_COLOR = (255, 100, 100)
BALL_COLOR = (100, 255, 100)


def load_video_frames(video_path, sample_fps=1, max_frames=0):
    """Extract frames from video at given sample rate."""
    cap = cv2.VideoCapture(video_path)
    original_fps = cap.get(cv2.CAP_PROP_FPS)

    if sample_fps <= 0 or sample_fps >= original_fps:
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
        if frame_count % frame_interval == 0:
            frames.append(
                Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            if max_frames > 0 and len(frames) >= max_frames:
                break
        frame_count += 1

    cap.release()
    return frames, original_fps, output_fps


def overlay_masks(frame, masks, colors, alpha=0.5):
    """Blend segmentation masks onto frame."""
    base = np.array(frame, dtype=np.float32) / 255.0
    overlay = base.copy()

    for obj_id, mask in masks.items():
        if mask is None:
            continue
        mask = np.squeeze(mask).clip(0, 1).astype(np.float32)
        color = np.array(colors.get(obj_id,
                                    (255, 0, 0)), dtype=np.float32) / 255.0
        m = mask[..., None]
        overlay = overlay * (1 - alpha * m) + color * (alpha * m)

    return Image.fromarray((overlay * 255).clip(0, 255).astype(np.uint8))


def save_video(frames, output_path, fps):
    """Write frames to video file."""
    if not frames:
        return
    h, w = np.array(frames[0]).shape[:2]
    out = cv2.VideoWriter(output_path, cv2.VideoWriter_fourcc(*'mp4v'), fps,
                          (w, h))
    for frame in frames:
        out.write(cv2.cvtColor(np.array(frame), cv2.COLOR_RGB2BGR))
    out.release()


def process_video(model,
                  processor,
                  video_path,
                  output_dir,
                  sample_fps=1,
                  max_frames=0):
    """Run SAM3 segmentation on video and save results."""
    video_name = Path(video_path).stem
    print(f"Processing: {video_name}")

    frames, original_fps, output_fps = load_video_frames(
        video_path, sample_fps, max_frames)
    if not frames:
        return {"video": video_name, "error": "Could not load video frames"}

    print(
        f"  {len(frames)} frames (sampled at {output_fps} fps from {original_fps} fps)"
    )

    session = processor.init_video_session(
        video=frames,
        inference_device="cuda",
        processing_device="cpu",
        video_storage_device="cpu",
        dtype=torch.bfloat16,
    )
    session = processor.add_text_prompt(inference_session=session, text=PROMPTS)

    masks_by_frame = {}
    obj_to_prompt = {}

    with torch.no_grad():
        for out in model.propagate_in_video_iterator(
                inference_session=session, max_frame_num_to_track=len(frames)):
            processed = processor.postprocess_outputs(session, out)
            frame_idx = out.frame_idx

            for prompt, ids in processed.get("prompt_to_obj_ids", {}).items():
                for obj_id in ids:
                    obj_to_prompt[int(obj_id)] = prompt

            frame_masks = {}
            for i, obj_id in enumerate(processed["object_ids"]):
                mask = processed["masks"][i].float().cpu().numpy()
                frame_masks[int(obj_id.item())] = (np.squeeze(mask) > 0).astype(
                    np.float32)
            masks_by_frame[frame_idx] = frame_masks

    colors = {}
    for obj_id, prompt in obj_to_prompt.items():
        if "player" in prompt.lower():
            colors[obj_id] = PLAYER_COLOR
        elif "ball" in prompt.lower():
            colors[obj_id] = BALL_COLOR

    output_frames = []
    for i, frame in enumerate(frames):
        masks = masks_by_frame.get(i, {})
        output_frames.append(
            overlay_masks(frame, masks, colors) if masks else frame)

    # Write to temp file first (cv2.VideoWriter doesn't work well with FUSE mounts)
    video_output_dir = output_dir / video_name
    video_output_dir.mkdir(parents=True, exist_ok=True)
    output_video_path = video_output_dir / f"{video_name}_segmented.mp4"

    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        save_video(output_frames, tmp_path, output_fps or 1.0)
        shutil.copy2(tmp_path, str(output_video_path))
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    prompts_lower = [p.lower() for p in obj_to_prompt.values()]
    total_players = sum("player" in p for p in prompts_lower)
    total_balls = sum("ball" in p for p in prompts_lower)

    result = {
        "video": video_name,
        "frames_processed": len(frames),
        "original_fps": original_fps,
        "output_fps": output_fps,
        "objects_detected": len(obj_to_prompt),
        "players_detected": total_players,
        "balls_detected": total_balls,
        "output_video": str(output_video_path),
    }

    with open(video_output_dir / f"{video_name}_metadata.json", 'w') as f:
        json.dump(result, f, indent=2)

    print(f"  Detected {total_players} player(s), {total_balls} ball(s)")
    print(f"  Saved to {output_video_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description='SAM3 video segmentation')
    parser.add_argument('video_path', help='Input video file')
    parser.add_argument('--output-dir', default='/outputs/segmentation_results')
    parser.add_argument('--sample-fps',
                        type=float,
                        default=1,
                        help='Sample rate (0=all frames)')
    parser.add_argument('--max-frames',
                        type=int,
                        default=0,
                        help='Max frames (0=unlimited)')
    args = parser.parse_args()

    video_path = Path(args.video_path)
    if not video_path.exists():
        print(f"Error: Video not found: {video_path}")
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Video: {video_path}")
    print(f"Output: {output_dir}")
    print(
        f"Sample FPS: {args.sample_fps}, Max frames: {args.max_frames or 'unlimited'}"
    )

    print("\nLoading SAM3 model...")
    model = Sam3VideoModel.from_pretrained("facebook/sam3").to(
        "cuda", dtype=torch.bfloat16).eval()
    processor = Sam3VideoProcessor.from_pretrained("facebook/sam3")
    print("Model loaded!")

    try:
        result = process_video(model, processor, str(video_path), output_dir,
                               args.sample_fps, args.max_frames)
        if "error" in result:
            print(f"Error: {result['error']}")
            return 1
        print("\nDone!")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1
    finally:
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    exit(main())
