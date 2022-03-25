"""skylet: a daemon running on the head node of a cluster."""

import time

from sky.skylet import job_lib

timestamp = time.strftime('[%Y-%m-%d %H:%M:%S]', time.localtime())
print(f'[{timestamp}] skylet started')
while True:
    time.sleep(20)
    job_lib.update_status()
