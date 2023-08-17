# SkyServe cancel example

This example demonstrates the redirect support canceling a request.

## Running the example

Under skypilot root directory, run the following command:

```bash
sky serve up sky/serve/examples/misc/cancel/service.yaml -n skyserve-cancel-test
```

Use `sky serve status` to monitor the status of the service. When its ready, run

```bash
sky serve logs skyserve-cancel-test 1
```

to monitor the logs of the service. Run

```bash
python3 sky/serve/examples/misc/cancel/send_cancel_request.py
```

and enter the endpoint output by `sky serve status`. You should see the following output:

```bash
Computing... step 0
Computing... step 1
Client disconnected, stopping computation.
```
