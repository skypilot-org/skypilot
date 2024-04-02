import requests

import sky
from sky.serve import serve_utils


class LocationTrustedRedirectSession(requests.sessions.Session):

    def request(self, method, url, location_trusted, *args, **kwargs):
        if not location_trusted:
            return super(LocationTrustedRedirectSession,
                         self).request(method, url, *args, **kwargs)
        kwargs['allow_redirects'] = False
        response = super(LocationTrustedRedirectSession,
                         self).request(method, url, *args, **kwargs)
        if response.is_redirect or response.status_code in (301, 302, 303, 307,
                                                            308):
            new_url = response.headers.get('Location', None)
            if new_url is not None:
                return super(LocationTrustedRedirectSession,
                             self).request(method, new_url, *args, **kwargs)
        return response


service_record = sky.serve.status('vllm')
endpoint = serve_utils.get_endpoint(service_record)
url = f'http://{endpoint}/v1/chat/completions'
data = {
    "model": "meta-llama/Llama-2-7b-chat-hf",
    "messages": [{
        "role": "user",
        "content": "Who are you?"
    }]
}
headers = {'Authorization': 'Bearer static_secret_token'}

session = LocationTrustedRedirectSession()
response = session.post(url, data=data, headers=headers, location_trusted=True)

print(response.status_code)
print(response.text)
