import time

import ray

@ray.remote
def sleep_task(sleep_time):
    time.sleep(sleep_time)
    return True

if __name__ == '__main__':
    ray.init(address='auto')