## Setup

1. Install skypilot package by following these [instructions](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html).

2. Run `git clone https://github.com/skypilot-org/skypilot.git && cd examples/stable_diffusion`

3. Run `sky launch -c stable-diffusion stable_diffusion_docker.yaml` 

4. Run `ssh -L 7860:localhost:7860 stable-diffusion`

5. Open [`http://localhost:7860/`](http://localhost:7860/) in browser.

6. Type in text prompt and click "Generate".

![Stable Diffusion Web Tool UI](assets/stable_diffusion_ui.png)

7. Once you are done, run `sky stop stable-diffusion` to stop the VM.

8. To restart VM, repeat steps 3 and 4.


## Usage Tips
 - Avoid exceeding 900x900 for image resolution due GPU memory constraints
 - You can toggle "Classifier Free Guidance Scale" to higher value to enforce adherence to prompt
 - Here are some good example text prompts (Classifier Free Guidance Scale = 7.5, sampling steps = 50):
   - "donkey playing poker"
   - "UC Berkeley student writing code on a laptop"
   - "Marvel vs. DC"
   - "corgi on Golden Gate Bridge"
   - "desert golf"
   - "Indian McDonald's"
   - "Elon Musk robot"
   - "mechanical heart"
   - "Batman in San Francisco"
   - "futuristic city in the sky and clouds"
   - "bear ballroom dancing"
   - "wall-e terminator"
   - "psychedelic Yosemite"
   - "rap song album cover"
   - "Wall Street bull rodeo"
   - "Trump in minecraft"
   
