import json
import os
import subprocess
import tempfile
import threading
import time
import queue

import psutil

SKY_REMOTE_BENCHMARK_DIR = '~/sky_benchmark_dir'
CONFIG = 'config.json'
TIMESTAMP_LOG = 'timestamps.log'
NUM_BYTES_PER_TIMESTAMP = 4
BYTE_ORDER = 'big'


class SkyCallback:

    def __init__(self,
                 log_dir=SKY_REMOTE_BENCHMARK_DIR,
                 max_queue_size=10,
                 flush_secs=10):
        self.start_ts = int(psutil.Process(os.getpid()).create_time())
        self.total_train_steps = None

        # Create a log directory.
        self.log_dir = os.path.expanduser(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)
        self._save_config()

        # Save the timestamps in the local storage.
        self._general_file_writer = tempfile.NamedTemporaryFile('w+b', delete=False)
        self._async_writer = _AsyncWriter(self._general_file_writer,
                                          max_queue_size, flush_secs)

        # Asynchronously copy the locally saved timestamps
        # to the cloud storage.
        self._async_copy_worker = _AsyncCopyWorker(
            self._general_file_writer.name,
            os.path.join(self.log_dir, TIMESTAMP_LOG),
            flush_secs,
        )
        self._async_copy_worker.start()

    def config(self, total_train_steps):
        self.total_train_steps = total_train_steps
        self._save_config()

    def _save_config(self):
        config = {
            'start_ts': self.start_ts,
            'total_steps': self.total_train_steps,
        }
        config_str = json.dumps(config)
        with open(os.path.join(self.log_dir, CONFIG), 'w') as f:
            f.write(config_str)

    def _save_timestamp(self, timestamp):
        # TODO: For generality, use protobuf to write the timestamp log.
        timestamp = timestamp.to_bytes(NUM_BYTES_PER_TIMESTAMP,
                                       byteorder=BYTE_ORDER)
        self._async_writer.write(timestamp)

    def on_train_step_begin(self):
        now = int(time.time())
        self._save_timestamp(now)

    def on_train_step_end(self):
        now = int(time.time())
        self._save_timestamp(now)


class _AsyncCopyWorker(threading.Thread):

    def __init__(self, src, dst, interval=10):
        threading.Thread.__init__(self)
        self.src = src
        self.dst = dst
        self.interval = interval

    def run(self):
        while True:
            subprocess.run(['cp', str(self.src), str(self.dst)], check=False)
            time.sleep(self.interval)

    def stop(self):
        self.join()


# FIXME: Check the license of the snippet below.
# From https://github.com/tensorflow/tensorboard/blob/master/tensorboard/summary/writer/event_file_writer.py
class _AsyncWriter(object):
    """Writes bytes to a file."""

    def __init__(self, record_writer, max_queue_size=20, flush_secs=120):
        """Writes bytes to a file asynchronously. An instance of this class
        holds a queue to keep the incoming data temporarily. Data passed to the
        `write` function will be put to the queue and the function returns
        immediately. This class also maintains a thread to write data in the
        queue to disk. The first initialization parameter is an instance of
        `tensorboard.summary.record_writer` which computes the CRC checksum and
        then write the combined result to the disk. So we use an async approach
        to improve performance.
        Args:
            record_writer: A RecordWriter instance
            max_queue_size: Integer. Size of the queue for pending bytestrings.
            flush_secs: Number. How often, in seconds, to flush the
                pending bytestrings to disk.
        """
        self._writer = record_writer
        self._closed = False
        self._byte_queue = queue.Queue(max_queue_size)
        self._worker = _AsyncWriterThread(self._byte_queue, self._writer,
                                          flush_secs)
        self._lock = threading.Lock()
        self._worker.start()

    def write(self, bytestring):
        """Enqueue the given bytes to be written asychronously."""
        with self._lock:
            if self._closed:
                raise IOError("Writer is closed")
            self._byte_queue.put(bytestring)

    def flush(self):
        """Write all the enqueued bytestring before this flush call to disk.
        Block until all the above bytestring are written.
        """
        with self._lock:
            if self._closed:
                raise IOError("Writer is closed")
            self._byte_queue.join()
            self._writer.flush()

    def close(self):
        """Closes the underlying writer, flushing any pending writes first."""
        if not self._closed:
            with self._lock:
                if not self._closed:
                    self._closed = True
                    self._worker.stop()
                    self._writer.flush()
                    self._writer.close()


class _AsyncWriterThread(threading.Thread):
    """Thread that processes asynchronous writes for _AsyncWriter."""

    def __init__(self, queue, record_writer, flush_secs):
        """Creates an _AsyncWriterThread.
        Args:
          queue: A Queue from which to dequeue data.
          record_writer: An instance of record_writer writer.
          flush_secs: How often, in seconds, to flush the
            pending file to disk.
        """
        threading.Thread.__init__(self)
        self.daemon = True
        self._queue = queue
        self._record_writer = record_writer
        self._flush_secs = flush_secs
        # The first data will be flushed immediately.
        self._next_flush_time = 0
        self._has_pending_data = False
        self._shutdown_signal = object()

    def stop(self):
        self._queue.put(self._shutdown_signal)
        self.join()

    def run(self):
        # Here wait on the queue until an data appears, or till the next
        # time to flush the writer, whichever is earlier. If we have an
        # data, write it. If not, an empty queue exception will be raised
        # and we can proceed to flush the writer.
        while True:
            now = time.time()
            queue_wait_duration = self._next_flush_time - now
            data = None
            try:
                if queue_wait_duration > 0:
                    data = self._queue.get(True, queue_wait_duration)
                else:
                    data = self._queue.get(False)

                if data is self._shutdown_signal:
                    return
                self._record_writer.write(data)
                self._has_pending_data = True
            except queue.Empty:
                pass
            finally:
                if data:
                    self._queue.task_done()

            now = time.time()
            if now > self._next_flush_time:
                if self._has_pending_data:
                    # Small optimization - if there are no pending data,
                    # there's no need to flush, since each flush can be
                    # expensive (e.g. uploading a new file to a server).
                    self._record_writer.flush()
                    self._has_pending_data = False
                # Do it again in flush_secs.
                self._next_flush_time = now + self._flush_secs
