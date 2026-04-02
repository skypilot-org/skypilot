"""Elastic Training Worker

Signals readiness via $SKYPILOT_APP_STATUS, then waits for the controller
to push training state. When state is received, runs one epoch of training
on its assigned data partition and returns the result.

App status transitions:
  "ready"    -> waiting for controller to push state
  "training" -> processing an epoch
  "ready"    -> done, waiting for next epoch
"""
import http.server
import json
import os
import random
import threading
import time

RANK = int(os.environ.get('SKYPILOT_NODE_RANK', -1))
STATUS_FILE = os.environ.get('SKYPILOT_APP_STATUS', '/tmp/skypilot_app_status')
epochs_done = 0


def set_status(status):
    """Write status to $SKYPILOT_APP_STATUS.

    The discovery server picks this up and patches the pod label,
    making it visible to the controller via /nodes.
    """
    with open(STATUS_FILE, 'w') as f:
        f.write(status)


class TrainHandler(http.server.BaseHTTPRequestHandler):
    """Receives training state from controller, runs training."""

    def do_POST(self):
        global epochs_done
        if self.path == '/start':
            body = json.loads(
                self.rfile.read(int(self.headers.get('Content-Length', 0))))
            epoch = body['epoch']
            partition = body['partition']
            total_partitions = body['total_partitions']
            peers = body.get('peers', [])

            print(f'  Epoch {epoch}: partition {partition}/'
                  f'{total_partitions}, {len(peers)} peers')

            set_status('training')

            # Simulate training on this partition
            samples = 1000 // total_partitions
            time.sleep(random.uniform(3.0, 6.0))
            loss = 2.0 / epoch + random.uniform(-0.1, 0.1)
            epochs_done += 1

            set_status('ready')

            result = json.dumps({
                'rank': RANK,
                'epoch': epoch,
                'partition': partition,
                'loss': loss,
                'samples': samples,
                'epochs_done': epochs_done,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(result)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def main():
    print(f'=== Worker {RANK} started ===')

    # Start HTTP server
    server = http.server.HTTPServer(('0.0.0.0', 8080), TrainHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # Signal ready to controller
    set_status('ready')
    print(f'Worker {RANK} ready on :8080')

    # Stay alive
    while True:
        time.sleep(30)
        print(f'Worker {RANK}: {epochs_done} epochs done')


if __name__ == '__main__':
    main()
