# Setup for Metrics Instance

To setup the metrics instance, we create a small instance with Pushgateway and Prometheus docker containers.

First, the security permissions of this instance should be set such that the 9090 and 9091 ports are publically accessible. Then mount/copy the `prometheus.yml` onto the instance. 

Finally, connect to the instance using SSH and set it up using:

```
sudo yum update -y
sudo amazon-linux-extras install docker
sudo service docker start
sudo systemctl enable docker
sudo usermod -a -G docker ec2-user
docker pull prom/pushgateway
```

Finally, to run the containers you can use:

```
sudo docker run -d -p 9091:9091 prom/pushgateway
sudo docker run -d -p 9090:9090 -v /home/ec2-user/prometheus.yml:/etc/prometheus/prometheus.yml prom/prometheus
```


