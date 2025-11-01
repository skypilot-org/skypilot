//! ResNet Training Example (resnet_app.yaml)
//!
//! SkyPilot: examples/resnet_app.yaml

use styx_core::{SkyTask, SkyResources, launch};

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    // YAML equivalent:
    // name: resnet-app
    // 
    // resources:
    //   accelerators: V100:1
    // 
    // setup: |
    //   git clone https://github.com/michaelzhiluo/pytorch-resnet /tmp/resnet || true
    //   cd /tmp/resnet
    //   conda activate resnet
    //   if [ $? -eq 0 ]; then
    //     echo 'conda env exists'
    //   else
    //     conda create -n resnet python=3.7 -y
    //     conda activate resnet
    //     pip install pytorch==1.7.1+cu110 torchvision==0.8.2+cu110 -f https://download.pytorch.org/whl/torch_stable.html
    //   fi
    // 
    // run: |
    //   cd /tmp/resnet
    //   conda activate resnet
    //   python train.py --data cifar --epochs 20 --batch-size 128

    let task = SkyTask::new()
        .with_name("resnet-app")
        .with_setup(r#"
            git clone https://github.com/michaelzhiluo/pytorch-resnet /tmp/resnet || true
            cd /tmp/resnet
            conda activate resnet
            if [ $? -eq 0 ]; then
              echo 'conda env exists'
            else
              conda create -n resnet python=3.7 -y
              conda activate resnet
              pip install pytorch==1.7.1+cu110 torchvision==0.8.2+cu110 \
                -f https://download.pytorch.org/whl/torch_stable.html
            fi
        "#)
        .with_run(r#"
            cd /tmp/resnet
            conda activate resnet
            python train.py --data cifar --epochs 20 --batch-size 128
        "#);

    let resources = SkyResources::new()
        .with_accelerator("V100", 1);

    let task = task.with_resources(resources);

    let task_id = launch(task, None, false).await?;

    println!("? ResNet training launched: {}", task_id);

    Ok(())
}
