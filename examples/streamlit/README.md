# Streamlit + SkyPilot

Deploy your Streamlit app with SkyPilot.

![Streamlit Demo](https://raw.githubusercontent.com/skypilot-org/skypilot/master/examples/streamlit/image.png)

## Quick start

Launch the app using [`streamlit.sky.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/streamlit/streamlit.sky.yaml):
```bash
sky launch -c streamlit-app streamlit.sky.yaml # specify "--infra aws" or other cloud if desired
```

> **Tip:** Add `-d` to detach and return to your terminal immediately:
> `sky launch -c streamlit-app streamlit.sky.yaml -d`

Get the endpoint URL:
```bash
ENDPOINT=$(sky status --endpoint 8501 streamlit-app)
echo "Streamlit app: $ENDPOINT"
```

Open the URL in your browser to access your app.

## Using your own Streamlit app

1. **Replace [`app.py`](https://github.com/skypilot-org/skypilot/blob/master/examples/streamlit/app.py)** with your own Streamlit application
2. **Update [`streamlit.sky.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/streamlit/streamlit.sky.yaml)**:
   - Add any dependencies to the `setup` section
   - Modify the `run` command if your app has a different filename
3. **Launch**:
   ```bash
   sky launch -c my-app streamlit.sky.yaml
   ```

### Example with requirements.txt

If your app needs additional packages, update the YAML:

```yaml
setup: |
  pip install streamlit
  pip install -r requirements.txt
```

## Teardown

Stop the cluster when done:
```bash
sky down streamlit-app
```

## Advanced options

### Custom port
```yaml
resources:
  ports: 8080

run: |
  streamlit run app.py --server.port 8080 --server.address 0.0.0.0
```

### Specific cloud/instance
```yaml
resources:
  infra: aws # or "infra: aws/us-east-1" or "infra: gcp" or "infra: azure" etc.
  instance_type: t3.medium
  ports: 8501
```

## Files

- [`app.py`](https://github.com/skypilot-org/skypilot/blob/master/examples/streamlit/app.py) - Simple Streamlit demo application
- [`streamlit.sky.yaml`](https://github.com/skypilot-org/skypilot/blob/master/examples/streamlit/streamlit.sky.yaml) - SkyPilot task configuration

## Learn more

- [SkyPilot Documentation](https://skypilot.readthedocs.io/)
- [Streamlit Documentation](https://docs.streamlit.io/)
