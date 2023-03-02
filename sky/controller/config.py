import ssl

ENABLE_MUTUAL_TLS = False

if ENABLE_MUTUAL_TLS:
    SERVER_SSL_CONFIG = dict(ssl_certfile='certificates/server.crt',
                             ssl_keyfile='certificates/server.key',
                             ssl_cert_reqs=ssl.CERT_REQUIRED,
                             ssl_ca_certs='certificates/ca.client.crt')
    CLIENT_SSL_CONFIG = dict(verify='certificates/ca.crt',
                             cert=('certificates/client_0.crt',
                                   'certificates/client_0.key'))
    URL = 'https://localhost:8080'
else:
    SERVER_SSL_CONFIG = {}
    CLIENT_SSL_CONFIG = {}
    URL = 'http://localhost:8080'
