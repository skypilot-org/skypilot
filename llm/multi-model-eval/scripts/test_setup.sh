#!/bin/bash
# Test script to verify setup before running evaluations

echo "🔍 Checking Multi-Model Evaluation Setup"
echo "========================================"

# Check Python
echo -n "✓ Python 3.8+: "
python3 --version

# Check SkyPilot
echo -n "✓ SkyPilot: "
if command -v sky &> /dev/null; then
    sky --version
else
    echo "❌ Not installed. Run: pip install skypilot[all]"
fi

# Check Promptfoo
echo -n "✓ Promptfoo: "
if command -v promptfoo &> /dev/null; then
    promptfoo --version
else
    echo "❌ Not installed. Run: npm install -g promptfoo"
fi

# Check cloud credentials
echo -e "\n📋 Cloud Access:"
sky check

# Check required files
echo -e "\n📁 Required Files:"
for file in serve-template.yaml models_config.yaml evaluate_models.py; do
    if [ -f "$file" ]; then
        echo "✓ $file"
    else
        echo "❌ Missing: $file"
    fi
done

echo -e "\n🎯 Next Steps:"
echo "1. Configure your models in models_config.yaml"
echo "2. Upload checkpoints using prepare_checkpoints.py"
echo "3. Run: python evaluate_models.py"