This example is using the same config located https://github.com/OpenAccess-AI-Collective/axolotl/tree/main/examples/mistral



Simple training example
```
sky launch -c axolotl axolotl.yaml -y
ssh -L 8888:localhost:8888 axolotl
sky down axolotl -y
```



If you want to add use_spot: True in resources section. this won't support auto resume
```
sky launch -c axolotl-spot axolotl-spot.yaml -y
ssh -L 8888:localhost:8888 axolotl-spot
```


Launch spot instances
```
sky spot launch -n axolotl-spot axolotl-spot.yaml -y

```