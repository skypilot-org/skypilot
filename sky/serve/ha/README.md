# SkyServe LoadBalancer HA Prototype

This is a prototype of providing HA for SkyServe load balancers by creating 
multiple replicas.

This prototype separates out the SkyServe load balancer from the controller and 
runs it as a separate deployment, which can be scaled to multiple replicas to 
maximize availability.

<!-- https://docs.google.com/drawings/d/1gzhDiOCGRpF6Oi2nGdqLIsKSCzQBf5aE5zoV37matfg/edit?usp=sharing -->
<img src="https://i.imgur.com/BUw1tYZ.jpeg">

**Features**
* High availablity - if a load balancer pod fails, it is automatically replaced. Rest of the load balancers continue to serve traffic.
* Supports rolling updates for your latest custom load balancing policies with zero downtime
* Always a single endpoint for the clients to connect to. IP will never change.

## How to run HA load balancer

1. Build and push your custom load balancer image. All your changes in the local sky repo will be added to this image.
    ```
    ./build_ha_image.sh IMAGE_NAME
    ```

2. Edit `lb-ha-deployment.yaml` and change load balancer image and replica counts if desired.
3. Deploy your SkyServe YAML
    ```bash
    sky serve up -n http http_server.yaml
    ```
4. Apply the HA load balancer deployment, service and ingress rule:
    ```bash
    kubectl apply -f lb-ha-deployment.yaml
    ```
5. Get the IP of your ingress using `kubectl get ingress sky-lb-ha-ingress` and test the load balancer by sending requests to the `/sky-lb-ha` ingress endpoint.
    ```console
    curl -L http://<ingress-ip>/sky-lb-ha
    ```
   
## How to scale the load balancer

You can scale the load balancer by changing the number of replicas in the deployment.
```bash
kubectl scale deployment sky-lb-ha --replicas=3
```

## How to perform a rolling update

1. Make changes to your custom load balancer policies and rebuild the image with a new tag `./build_ha_image.sh myrepo/myimage:v2`.
2. Update the deployment with the new image
    ```bash
    kubectl set image deployment/sky-lb-ha-deployment sky-lb-ha=myrepo/myimage:v2
    ```
3. Monitor the rolling update with `kubectl get pods -w` and `kubectl get deployment sky-lb-ha -w`. The load balancer will continue to serve traffic during the update.


## How to test HA

1. Send a request to the load balancer endpoint
    ```bash
    curl -L http://<ingress-ip>/sky-lb-ha
    ```
   
2. Delete one of the load balancer pods
    ```bash
    kubectl delete pod <pod-name>
    ```
   
3. Send another request to the load balancer endpoint
    ```bash
    curl -L http://<ingress-ip>/sky-lb-ha
    ```

## Caveats
* Stateful load balancer policies are tricky. Some of them may work in an approximate manner - need to test.