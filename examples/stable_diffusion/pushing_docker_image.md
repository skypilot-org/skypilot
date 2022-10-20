## GCR

1. Install skypilot package by following these [instructions](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

2. Run `git clone https://github.com/skypilot-org/skypilot.git `.

3. Steps 4-6 are based on these [GCR setup instructions](https://cloud.google.com/container-registry/docs/pushing-and-pulling)

4. First [enable container registry](https://cloud.google.com/container-registry/docs/enable-service) in your project

5. Next follow these [steps](https://cloud.google.com/container-registry/docs/advanced-authentication) to install and authenticate Docker on your CLI

6. Ensure that you have [Storage Admin](https://cloud.google.com/storage/docs/access-control/iam-roles) permission in your project

7. Run `cd skypilot/examples/stable_diffusion` and `docker build Dockerfile`

8. Run `docker tag stable-diffusion-webui-docker_model gcr.io/(my-project)/stable-diffusion`.

9. Run `docker push gcr.io/(my-project)/stable-diffusion` and verify on GCR that the image is there.

## DockerHub

1. Make a free [DockerHub](https://hub.docker.com/) account.

2. In your CLI, run `docker login` and use the login credentials from your DockerHub account.

3. Run `cd skypilot/examples/stable_diffusion` and `docker build Dockerfile`

4. Run `docker tag stable-diffusion-webui-docker_model (my-dockerhub-username)/stable-diffusion`.

5. Run `docker push (my-dockerhub-username)/stable-diffusion` and verify on Dockerhub that the image is there.



