"""
DeepSeek OCR Image Processing Script
Processes images from the Book-Scan-OCR dataset.
"""

import argparse
import json
from pathlib import Path

import torch
from transformers import AutoModel
from transformers import AutoTokenizer


def main():
    parser = argparse.ArgumentParser(description='Process OCR on image dataset')
    parser.add_argument('--start-idx', type=int, required=True)
    parser.add_argument('--end-idx', type=int, required=True)
    args = parser.parse_args()

    print(f"Processing range: {args.start_idx} to {args.end_idx}")

    # Load DeepSeek OCR model
    model_name = "deepseek-ai/deepseek-ocr"
    tokenizer = AutoTokenizer.from_pretrained(model_name,
                                              trust_remote_code=True)

    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModel.from_pretrained(model_name,
                                      _attn_implementation='flash_attention_2',
                                      trust_remote_code=True,
                                      use_safetensors=True)
    model = model.eval().cuda().to(torch.bfloat16)

    # Find and slice images
    image_dir = Path.cwd() / "book-scan-ocr" / "Book-Scan-OCR" / "images"
    output_dir = Path("/outputs/ocr_results")
    output_dir.mkdir(parents=True, exist_ok=True)

    all_image_files = sorted(image_dir.glob("*.jpg")) + sorted(
        image_dir.glob("*.png"))
    image_files = all_image_files[args.start_idx:args.end_idx]

    print(f"Processing {len(image_files)} images...")

    results = []
    for idx, img_path in enumerate(image_files, 1):
        print(f"Processing {idx}/{len(image_files)}: {img_path.name}...")

        try:
            # Run OCR with grounding tag for structure awareness
            prompt = "<image>\\n<|grounding|>Convert the document to markdown. "
            image_output_dir = output_dir / img_path.stem
            image_output_dir.mkdir(exist_ok=True)

            ocr_result = model.infer(tokenizer,
                                     prompt=prompt,
                                     image_file=str(img_path),
                                     output_path=str(image_output_dir),
                                     base_size=1024,
                                     image_size=640,
                                     crop_mode=True,
                                     save_results=True,
                                     test_compress=True)

            # Read the markdown result
            mmd_file = image_output_dir / "result.mmd"
            if mmd_file.exists():
                with open(mmd_file, 'r', encoding='utf-8') as f:
                    ocr_text = f.read()
            else:
                ocr_text = "[OCR completed but result not found]"

            # Save consolidated markdown at top level
            md_file = output_dir / f"{img_path.stem}.md"
            with open(md_file, 'w', encoding='utf-8') as f:
                f.write(f"# {img_path.name}\\n\\n{ocr_text}\\n")

            # Save JSON metadata
            result = {"image_name": img_path.name, "ocr_text": ocr_text}
            results.append(result)

            json_file = output_dir / f"{img_path.stem}_ocr.json"
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)

            print(f"Saved markdown to {md_file}")

        except Exception as e:
            print(f"Error processing {img_path.name}: {e}")
            results.append({"image_name": img_path.name, "error": str(e)})

    # Save batch summary
    summary_file = output_dir / f"results_{args.start_idx}_{args.end_idx}.json"
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Print summary
    successful = sum(1 for r in results if "error" not in r)
    print(f"\\n{'='*60}")
    print(f"Processing complete!")
    print(
        f"Total: {len(results)} | Successful: {successful} | Failed: {len(results) - successful}"
    )
    print(f"Results saved to {output_dir}")
    print('=' * 60)


if __name__ == "__main__":
    main()
