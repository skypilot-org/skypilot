import sky.server.requests.executor as executor
from multiprocessing import synchronize

synchronize.Semaphore(1).acquire()
