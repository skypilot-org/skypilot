# Setup for Metrics Instance

To setup the metrics instance, we create a small instance with Pushgateway and Prometheus docker containers.

1. Launch the user stats collection server with `sky launch sky/utils/user_stats/user_stats_server.yaml`.
2. The security permissions of this instance should be set such that the 9090 and 9091 ports are publically accessible. It can be done by replacing the security group on the console.
3. Update the `[ip address]` in [sky/utils/user_stats/prometheus_config.yml](sky/utils/user_stats/prometheus_config.yml) and the `PROM_PUSHGATEWAY_URL` in [sky/utils/user_stats/metrics.py](sky/utils/user_stats/metrics.py) with the instance public ip address.
4. Copy the [sky/utils/user_stats/prometheus_config.yml](sky/utils/user_stats/prometheus_config.yml) to the instance.
5. Finally, run the services:

```
sudo docker run -d -p 9091:9091 prom/pushgateway
sudo docker run -d -p 9090:9090 -v /home/ubuntu/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus
```


