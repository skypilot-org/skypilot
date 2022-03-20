import time
from sky.skylet import job_lib

while True:
    time.sleep(20)
    job_lib.update_status()

