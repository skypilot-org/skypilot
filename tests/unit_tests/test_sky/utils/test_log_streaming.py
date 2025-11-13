"""Regression test for log streaming with infrequent output."""

import os
import subprocess
import tempfile
import threading
import time
import unittest

from sky.utils import context
from sky.utils import context_utils


class TestLogStreaming(unittest.TestCase):
    """Regression test for the jupyter lab log streaming issue."""

    def test_logs_flushed_promptly_with_infrequent_output(self):
        """Regression test: Logs should be flushed promptly even with infrequent output.

        Bug: When running 'sky logs' on a long-running task (like jupyter lab)
        that emits logs infrequently, logs would not appear until much later or
        until the task emitted more logs, because the flush logic wasn't executing.

        Expected behavior: Logs should be flushed periodically (within ~0.5s) even
        when the task continues running without producing more logs.
        """
        context.initialize()
        ctx = context.get()

        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp:
            tmp_path = tmp.name

        try:
            with open(tmp_path, 'w') as output_file:

                def custom_handler(in_stream, out_stream):
                    return context_utils.passthrough_stream_handler(
                        in_stream, output_file)

                # Simulate jupyter: emit log then continue running for a while
                script = (
                    'import sys, time; '
                    'print(\'Server started\', flush=True); '
                    'time.sleep(3.0)'  # Continue running but no more logs
                )

                proc = subprocess.Popen(['python', '-c', script],
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE)

                # Start handler
                def run_handler():
                    context_utils.pipe_and_wait_process(
                        ctx, proc, stdout_stream_handler=custom_handler)

                handler_thread = threading.Thread(target=run_handler,
                                                  daemon=True)
                handler_thread.start()

                # Check if log appears within reasonable time
                start = time.time()
                log_appeared = False

                for _ in range(35):  # Check for 3.5 seconds
                    time.sleep(0.1)
                    try:
                        with open(tmp_path, 'r') as f:
                            content = f.read()
                            if 'Server started' in content:
                                log_appeared = True
                                break
                    except:
                        pass

                elapsed_until_log = time.time() - start

                # Wait for handler to complete
                handler_thread.join(timeout=5.0)

            # Verify log was captured
            with open(tmp_path, 'r') as f:
                output = f.read()
            self.assertIn('Server started', output)

            # Key assertion: Log should appear within ~1s, not at the end (3s+)
            # With proper flushing: appears within 0.5-1s (flush interval is 0.5s)
            # Without flushing: appears only when process ends (~3s)
            self.assertTrue(
                log_appeared,
                'Log should appear in output file while task is still running')
            self.assertLess(
                elapsed_until_log, 1.0,
                f'Log took {elapsed_until_log:.2f}s to appear. Expected prompt flush '
                f'within ~0.5-1s, but log didn\'t appear until near process completion (3s).'
            )

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


if __name__ == '__main__':
    unittest.main()
