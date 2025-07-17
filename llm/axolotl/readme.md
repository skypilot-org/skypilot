# Axolotl

This example is using the same config located https://github.com/OpenAccess-AI-Collective/axolotl/tree/main/examples/mistral



Simple training example
```
HF_TOKEN=xxx sky launch -c axolotl axolotl.yaml --secret HF_TOKEN -y -i30 --down
ssh -L 8888:localhost:8888 axolotl
sky down axolotl -y
```



To launch an unmanaged spot instance (no auto-recovery; good for debugging)
```
HF_TOKEN=xxx BUCKET=<unique-name> sky launch -c axolotl-spot axolotl-spot.yaml --secret HF_TOKEN --env BUCKET -i30 --down
ssh -L 8888:localhost:8888 axolotl-spot
```


Launch managed spot instances (auto-recovery; for full runs):
```
HF_TOKEN=xxx BUCKET=<unique-name> sky jobs launch -n axolotl-spot axolotl-spot.yaml --secret HF_TOKEN --env BUCKET
```
