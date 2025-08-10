#!/bin/bash
# Quick setup script for the demo

echo "🚀 Setting up Multi-Model Evaluation Demo"
echo "========================================"

# Check if connected to GKE
echo "1. Checking GKE connection..."
kubectl get nodes > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "⚠️  Not connected to GKE. Running connection command..."
    gcloud container clusters get-credentials zhwu-api-test --region us-central1-c
else
    echo "✅ Connected to GKE cluster"
fi

# Check if volume exists
echo ""
echo "2. Checking for model-checkpoints volume..."
sky volumes ls | grep -q "model-checkpoints"
if [ $? -ne 0 ]; then
    echo "⚠️  Volume not found. Creating it..."
    sky volumes apply finetuning/create-volume.yaml
else
    echo "✅ Volume 'model-checkpoints' exists"
fi

# Check if model exists in volume
echo ""
echo "3. Checking if fine-tuned model exists in volume..."
echo "   (This step requires running the fine-tuning first)"

# Option to run quick fine-tuning
echo ""
echo "4. Would you like to run a quick fine-tuning demo? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    echo "🔄 Launching fine-tuning job..."
    sky launch finetuning/finetune-to-volume.yaml -c finetune-demo -y
    echo ""
    echo "⏳ Waiting for fine-tuning to complete (this may take 5-10 minutes)..."
    echo "   You can monitor with: sky logs finetune-demo --follow"
else
    echo "⏭️  Skipping fine-tuning. Make sure the model exists in the volume!"
fi

echo ""
echo "✅ Setup complete! You're ready for the demo."
echo ""
echo "Next steps:"
echo "1. Run the evaluation: python evaluate_models.py"
echo "2. View results: promptfoo view"
echo ""
echo "💡 Tip: The models_config.yaml is configured with:"
echo "   - Qwen 2.5 from HuggingFace"
echo "   - Qwen fine-tuned from volume" 
echo "   - Mistral from HuggingFace"