//! Batch inference utilities

use anyhow::Result;
use serde::{Deserialize, Serialize};
use styx_sky::{launch, Resources, Storage, StorageMode, Task};

/// Batch inference configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchInferenceConfig {
    pub model: String,
    pub input_path: String,
    pub output_path: String,
    pub batch_size: usize,
    pub max_tokens: usize,
    pub temperature: f32,
    pub backend: String,
}

impl Default for BatchInferenceConfig {
    fn default() -> Self {
        Self {
            model: "".to_string(),
            input_path: "".to_string(),
            output_path: "".to_string(),
            batch_size: 32,
            max_tokens: 512,
            temperature: 0.7,
            backend: "vllm".to_string(),
        }
    }
}

/// Batch inference runner
pub struct BatchInference {
    config: BatchInferenceConfig,
}

impl BatchInference {
    pub fn new(config: BatchInferenceConfig) -> Self {
        Self { config }
    }

    /// Run batch inference
    pub async fn run(&self) -> Result<String> {
        // Mount input/output storage
        let input_storage = Storage::new("s3", &self.config.input_path, StorageMode::MOUNT);
        let output_storage = Storage::new("s3", &self.config.output_path, StorageMode::MOUNT);

        let task = Task::new()
            .with_name("batch-inference")
            .with_setup(&self.generate_setup())
            .with_run(&self.generate_run())
            .with_input(input_storage)
            .with_output(output_storage)
            .with_resources(Resources::new().with_accelerator("A100", 4));

        launch(task, None, false).await
    }

    fn generate_setup(&self) -> String {
        r#"
# Create environment
conda activate inference || conda create -n inference python=3.10 -y
conda activate inference

# Install dependencies
pip install vllm torch transformers
pip install pandas tqdm
"#
        .to_string()
    }

    fn generate_run(&self) -> String {
        format!(
            r#"
python -c "
import json
from pathlib import Path
from vllm import LLM, SamplingParams
from tqdm import tqdm

# Load inputs
input_path = Path('/inputs')
output_path = Path('/outputs')

# Read input file
with open(input_path / 'prompts.jsonl') as f:
    prompts = [json.loads(line)['prompt'] for line in f]

# Initialize LLM
llm = LLM(model='{}', tensor_parallel_size=4)
sampling_params = SamplingParams(
    temperature={},
    max_tokens={},
)

# Run inference in batches
outputs = []
for i in tqdm(range(0, len(prompts), {})):
    batch = prompts[i:i+{}]
    results = llm.generate(batch, sampling_params)
    outputs.extend(results)

# Save outputs
with open(output_path / 'outputs.jsonl', 'w') as f:
    for prompt, output in zip(prompts, outputs):
        result = {{
            'prompt': prompt,
            'output': output.outputs[0].text,
            'tokens': len(output.outputs[0].token_ids)
        }}
        f.write(json.dumps(result) + '\\n')

print(f'Processed {{len(prompts)}} prompts')
"
"#,
            self.config.model,
            self.config.temperature,
            self.config.max_tokens,
            self.config.batch_size,
            self.config.batch_size,
        )
    }
}

/// Embedding generation for RAG
pub struct EmbeddingGenerator {
    model: String,
    input_path: String,
    output_path: String,
}

impl EmbeddingGenerator {
    pub fn new(
        model: impl Into<String>,
        input_path: impl Into<String>,
        output_path: impl Into<String>,
    ) -> Self {
        Self {
            model: model.into(),
            input_path: input_path.into(),
            output_path: output_path.into(),
        }
    }

    pub async fn generate(&self) -> Result<String> {
        let input_storage = Storage::new("s3", &self.input_path, StorageMode::MOUNT);
        let output_storage = Storage::new("s3", &self.output_path, StorageMode::MOUNT);

        let task = Task::new()
            .with_name("generate-embeddings")
            .with_setup(
                r#"
conda activate embeddings || conda create -n embeddings python=3.10 -y
conda activate embeddings
pip install sentence-transformers torch
"#,
            )
            .with_run(&format!(
                r#"
python -c "
from sentence_transformers import SentenceTransformer
import json
from pathlib import Path
import numpy as np

model = SentenceTransformer('{}')

# Load texts
input_path = Path('/inputs')
output_path = Path('/outputs')

with open(input_path / 'texts.jsonl') as f:
    texts = [json.loads(line)['text'] for line in f]

# Generate embeddings
embeddings = model.encode(texts, show_progress_bar=True)

# Save embeddings
np.save(output_path / 'embeddings.npy', embeddings)

with open(output_path / 'metadata.json', 'w') as f:
    json.dump({{
        'model': '{}',
        'count': len(texts),
        'dimension': embeddings.shape[1]
    }}, f)
"
"#,
                self.model, self.model
            ))
            .with_input(input_storage)
            .with_output(output_storage)
            .with_resources(Resources::new().with_accelerator("T4", 1));

        launch(task, None, false).await
    }
}
