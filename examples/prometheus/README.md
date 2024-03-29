# Exporting SkyPilot metrics to Prometheus

<img src="https://i.imgur.com/25CYKcD.png" alt="Prometheus Screenshot" width="1000"/>

This README showcases a simple example of how metrics can be extracted from 
SkyPilot and exported to Prometheus.

In `skypilot_prometheus_server.py`, we use the `prometheus_client` library to create a Prometheus client that 
exposes the following SkyPilot metrics:

1. `num_clusters`: Number of on-demand clusters currently running
2. `num_nodes`: Number of on-demand nodes currently running
3. `num_spot_jobs`: Number of total spot jobs
4. `num_running_spot_jobs`: Number of spot jobs in running state
5. `num_failed_spot_jobs`: Number of spot jobs that failed
6. `num_succeeded_spot_jobs`: Number of spot jobs that succeeded
7. `num_preemptions_spot_jobs`: Total number of preemptions that occurred across all jobs

## Running the example
1. Setup SkyPilot and run a few clusters with `sky launch` and some spot jobs with `sky spot launch` to generate some state in SkyPilot.
2. Run the metrics server with `python skypilot_prometheus_server.py`
    ```bash
    $ python skypilot_prometheus_server.py
   
    Updating metrics
    Number of clusters: 2
    Number of nodes: 2
    Number of spot jobs: 4
    Number of running spot jobs: 0
    Number of failed spot jobs: 1
    Number of succeeded spot jobs: 3
    Number of preemptions: 0
    ```
3. Visit `http://localhost:9000` to make sure the metrics are exposed.
   <img src="https://i.imgur.com/bkdXf1o.png" alt="Metrics server" width="500"/>
4. To import these metrics into Prometheus, add the following to your `prometheus.yml`:
    ```yaml
    scrape_configs:
      - job_name: 'skypilot'
        static_configs:
          - targets: ['host.docker.internal:9000']
    ```
   For the purposes of this example, we already provide a preconfigured `prometheus.yml`. 
   
   Note that we are assuming that Prometheus is running in a Docker container and the host machine is running the metrics server. 
   If Prometheus is running on a different machine than the metrics server, replace `host.docker.internal` with the ip of the machine running the metrics server.
5. Restart or run Prometheus with the updated configuration:
    ```bash
    $ docker run -p 9090:9090 -v $(pwd)/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus
    ```
6. Visit http://localhost:9090 to view the Prometheus dashboard. Under the `Status->Targets`, you should see `skypilot` as a target.

You can write queries and create graphs at http://localhost:9090/graph. For more powerful dashboards, consider using Grafana with Prometheus as the data source.