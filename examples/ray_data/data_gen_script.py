"""
Synthetic multi-QA generation from WikiText using Ray Data.

This script:
1. Loads 1K examples from the Salesforce/wikitext dataset.
2. For each text chunk, generates multiple synthetic
   Question + Answer pairs in structured JSON.
3. Uses vLLM via Ray Data's LLM processor for high throughput.
4. Writes the resulting QA dataset to disk.
"""

if __name__ == "__main__": # https://docs.vllm.ai/en/latest/usage/troubleshooting/#python-multiprocessing
    import ray
    from pydantic import BaseModel
    from typing import List

    from ray.data.llm import build_processor, vLLMEngineProcessorConfig
    from huggingface_hub import HfFileSystem

    print("Imports Done")

    # --------------------------------------------------------------------
    # 1. Define structured output schema (MULTI QA FORMAT)
    # --------------------------------------------------------------------

    class QA(BaseModel):
        question: str
        answer: str


    class QAList(BaseModel):
        qas: List[QA]  # Model can generate as many as it wants


    json_schema = QAList.model_json_schema()

    print(json_schema)

    # --------------------------------------------------------------------
    # 2. Initialize Ray
    # --------------------------------------------------------------------
    ray.init()

    print("Ray Initialized")

    # ------------------------------------------------------------------
    # 3. Load specific WikiText parquet file from HF
    # ------------------------------------------------------------------

    ds = ray.data.read_parquet(
        "hf://datasets/Salesforce/wikitext/wikitext-103-raw-v1/train-00000-of-00002.parquet",
        filesystem=HfFileSystem(),
    )

    print(f"Dataset count: {ds.count()}")
    print(ds.schema())

    # Filter + limit to 1K examples for this demo (you can increase or remove the limit for more data)
    ds = (
        ds.filter(lambda row: row["text"] is not None and len(row["text"].strip()) > 200)
        .limit(1_000)
    )

    # --------------------------------------------------------------------
    # 4. Configure vLLM engine
    # --------------------------------------------------------------------
    processor_config = vLLMEngineProcessorConfig(
        model_source="unsloth/Llama-3.2-1B-Instruct",
        engine_kwargs=dict(
            structured_outputs_config={"backend": "xgrammar"},
            dtype="bfloat16",
            max_model_len=4096,
        ),
        batch_size=32,
        concurrency=1,
    )

    # --------------------------------------------------------------------
    # 5. Build processor
    # --------------------------------------------------------------------
    def preprocess(row):
        context = row["text"][:800]

        return dict(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are generating synthetic QA training data.\n"
                        "Given a Wikipedia passage, generate as many high-quality "
                        "question-answer pairs as possible.\n"
                        "Requirements:\n"
                        "- Questions must be clear and specific.\n"
                        "- Answers must be grounded in the passage.\n"
                        "- Avoid trivial or redundant questions.\n"
                        "Return JSON with field: qas (a list of question/answer pairs)."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Passage:\n{context}",
                },
            ],
            sampling_params=dict(
                temperature=0.7,
                max_tokens=800,  # Increased for multiple QAs
                detokenize=False,
                structured_outputs=dict(json=json_schema),
            ),
        )


    def postprocess(row):
        return {
            "qa_json": row["generated_text"],
        }


    processor = build_processor(
        processor_config,
        preprocess=preprocess,
        postprocess=postprocess,
    )

    # --------------------------------------------------------------------
    # 6. Apply processor (lazy execution)
    # --------------------------------------------------------------------
    ds = processor(ds)

    # Trigger execution
    ds = ds.materialize()

    # --------------------------------------------------------------------
    # 7. Inspect a few outputs
    # --------------------------------------------------------------------
    for row in ds.take(3):
        print(row["qa_json"])
        print("=" * 80)

    # --------------------------------------------------------------------
    # 8. Persist generated multi-QA dataset
    # --------------------------------------------------------------------
    output_path = "outputs/synthetic_wikitext_multi_qa"

    ds.write_json(output_path)

    print(f"\nSaved 10K synthetic multi-QA examples to: {output_path}")

    # --------------------------------------------------------------------
    # 9. Shutdown Ray
    # --------------------------------------------------------------------

    ray.shutdown()