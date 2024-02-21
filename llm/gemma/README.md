### Accessing the model


```bash
IP=$(sky status --ip gemma)

curl -L http://$IP:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
      "model": "google/gemma-7b",
      "prompt": "My favourite condiment is",
      "max_tokens": 25
  }'
```
