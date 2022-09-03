
Steps to run this example:

## Setup

1. Install skypilot by following these [instructions](https://skypilot.readthedocs.io/en/latest/getting-started/installation.html)

2. Run sky launch -c stable-diffusion stable_diffusion_docker.yaml

3. Run ssh -L 7860:localhost:7860 stable-diffusion

4. Open http://localhost:7860/ in browser.

5. Type in text prompt and click "Generate" 

## Usage Tips
 - Avoid exceeding 900x900 for image resolution due GPU memory constraints
 - You can toggle "Classifier Free Guidance Scale" to higher value to enforce adherence to prompt
 - Here are some good example text prompts (Classifier Free Guidance Scale = 7.5, sampling steps = 50):
   - "donkey playing poker"
   - "UC Berkeley student writing code on a laptop"
   - "Marvel vs. DC"
   - "toilet human"
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
