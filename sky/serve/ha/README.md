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

1. Build and push your custom load balancer image, and start the load balancer deployment. All your changes in the local sky repo will be added to this image.
    ```
    ./create_or_update_lb.sh <service-name> <docker-repo>
    ```
2. Get the IP of your ingress using `kubectl get ingress sky-lb-ha-ingress-<service_name>` and test the load balancer by sending requests to the `/sky-lb-ha-<service_name>` ingress endpoint.
    ```console
    curl -L http://<ingress-ip>/sky-lb-ha-<service_name>
    ```
   
## How to scale the load balancer

You can scale the load balancer by changing the number of replicas in the deployment.
```bash
kubectl scale deployment sky-lb-ha-<service_name> --replicas=3
```

## How to perform a rolling update

1. Make changes to your custom load balancer policies and rerun:
    ```
    ./create_or_update_lb.sh <service-name> <docker-repo>
    ```
2. Monitor the rolling update with `kubectl get pods -w` and `kubectl get deployment sky-lb-ha-<service_name> -w`. The load balancer will continue to serve traffic during the update.


## How to test HA for the load balancer

1. Send a request to the load balancer endpoint
    ```bash
    curl -L http://<ingress-ip>/sky-lb-ha-<service_name>
    ```
   
2. Delete one of the load balancer pods
    ```bash
    kubectl delete pod <pod-name>
    ```
   
3. Send another request to the load balancer endpoint
    ```bash
    curl -L http://<ingress-ip>/sky-lb-ha-<service_name>
    ```

## How to handle controller failure

If the controller fails, the load balancer will continue to serve traffic by sending them to the original replicas. In order to restart the controller:

1. Start the service with a **different** name. This is important to avoid conflicts with the existing service and downtime.
    ```
    sky serve up -n <service-name>-v2 your-service.yaml
    ```
2. Pointing the old load-balancer to the new service, once the new service is up and running.
    ```
    ./create_or_update_lb.sh <service-name> <docker-repo> <service-name>-v2
    ```
3. Once the load balancer is pointed to the new service (by checking the rolling update as shown [here](#how-to-perform-a-rolling-update)), delete the old replicas.
    ```
    kubectl get pods | grep <service-name>
    # Find the old services in the list and delete them
    kubectl delete pod <pod-name>
    ```


## Caveats
* Stateful load balancer policies are tricky. Some of them may work in an approximate manner - need to test.
* If possible, it is suggested to randomly pick a replica from the list of candidate replica in your policy to prevent all replicas from returning the same endpoint. 
