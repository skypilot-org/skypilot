//! Environment Variables Example
//!
//! SkyPilot: Using environment variables

use styx_core::{SkyTask, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: env-vars
    // 
    // envs:
    //   MODEL_NAME: gpt2
    //   BATCH_SIZE: "32"
    //   LEARNING_RATE: "0.001"
    // 
    // run: |
    //   echo "Training model: $MODEL_NAME"
    //   python train.py \
    //     --model $MODEL_NAME \
    //     --batch-size $BATCH_SIZE \
    //     --lr $LEARNING_RATE

    let task = SkyTask::new()
        .with_name("env-vars")
        .with_env("MODEL_NAME", "gpt2")
        .with_env("BATCH_SIZE", "32")
        .with_env("LEARNING_RATE", "0.001")
        .with_run(r#"
            echo "Training model: $MODEL_NAME"
            python train.py \
              --model $MODEL_NAME \
              --batch-size $BATCH_SIZE \
              --lr $LEARNING_RATE
        "#);

    let task_id = launch(task, None, false).await?;

    println!("? Task with env vars launched: {}", task_id);
    println!("   MODEL_NAME: gpt2");
    println!("   BATCH_SIZE: 32");
    println!("   LEARNING_RATE: 0.001");

    Ok(())
}
