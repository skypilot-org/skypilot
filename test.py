import sky
from sky.execution import Stage
import threading

def test_stuff():
    task = sky.Task(run='nvidia-smi')

    task.set_resources(
        sky.Resources(sky.GCP(), region='us-central1', accelerators='V100'))

    sky.launch(task, down=True,detach_setup=True, detach_run=True)
    
thr = threading.Thread(target=test_stuff, args=(), kwargs={})
thr.start() 
print(thr.is_alive()) # Will return whether foo is running currently