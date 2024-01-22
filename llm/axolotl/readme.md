This example is using the same config located https://github.com/OpenAccess-AI-Collective/axolotl/tree/main/examples/mistral



Simple training example
```
HF_TOKEN=abc sky launch -c axolotl axolotl.yaml --env HF_TOKEN -y -i30 --down
ssh -L 8888:localhost:8888 axolotl
sky down axolotl -y
```



To launch an unmanaged spot instance (no auto-recovery; good for debugging)
```
HF_TOKEN=abc BUCKET=<unique-name> sky launch -c axolotl-spot axolotl-spot.yaml --env HF_TOKEN --env BUCKET -i30 --down
ssh -L 8888:localhost:8888 axolotl-spot
```


Launch managed spot instances (auto-recovery; for full runs):
```
HF_TOKEN=abc BUCKET=<unique-name> sky spot launch -n axolotl-spot axolotl-spot.yaml --env HF_TOKEN --env BUCKET
```