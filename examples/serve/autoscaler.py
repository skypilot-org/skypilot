import time

import requests


def send_get_request(ip, port):
    url = f"http://{ip}:{port}"
    try:
        response = requests.get(url)
        # You can handle the response here if needed
        print(f"GET request sent to {url} | Status code: {response.status_code}")
    except requests.RequestException as e:
        print(f"Error sending GET request to {url}: {e}")

def main():
    ip_address = "34.69.231.131"
    port_number = 30001
    sending_frequency_seconds = 0.1  # Adjust the sending frequency as needed

    while True:
        send_get_request(ip_address, port_number)
        time.sleep(sending_frequency_seconds)

if __name__ == "__main__":
    main()