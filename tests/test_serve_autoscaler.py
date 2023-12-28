import argparse
import time

import requests


def send_get_request(ip, port):
    url = f"http://{ip}:{port}"
    try:
        response = requests.get(url)
        print(
            f"GET request sent to {url} | Status code: {response.status_code}")
    except requests.RequestException as e:
        print(f"Error sending GET request to {url}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Send GET requests to a specified IP address and port.")
    parser.add_argument("--ip-address",
                        help="The IP address to send requests to.")
    parser.add_argument("--port", type=int, help="The port number to use.")
    parser.add_argument(
        "--frequency",
        type=float,
        default=5,
        help="Sending frequency in seconds (default is 5 seconds).")

    args = parser.parse_args()

    ip_address = args.ip_address
    port = args.port
    sending_frequency_seconds = args.frequency

    while True:
        send_get_request(ip_address, port)
        time.sleep(sending_frequency_seconds)


if __name__ == "__main__":
    main()
