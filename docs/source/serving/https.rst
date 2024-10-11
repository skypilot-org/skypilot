.. _https:

HTTPS Encryption
================

SkyServe enables secure serving of models over HTTPS, which is essential for handling sensitive data or for models that require secure communication with other services. Currently, SkyServe supports HTTPS encrypted endpoint (for communication between the client and the load balancer); HTTPS between the load balancer and the service replicas is not yet supported.

HTTPS Encrypted Endpoint
------------------------

To create an HTTPS encrypted endpoint, you need to provide a certificate and a private key. Obtaining these from a trusted Certificate Authority (CA) is the most secure method. However, for development and testing purposes, you can generate a self-signed certificate and private key using the :code:`openssl` command-line tool. Here is an example of how to generate them:

.. code-block:: bash

  $ openssl req -x509 -newkey rsa:2048 -days 36500 -nodes -keyout <key-path> -out <cert-path>

You can provide these as files:

.. code-block:: yaml
  :emphasize-lines: 5-6

  # https.yaml
  service:
    readiness_probe: /
    tls:
      keyfile: <key-path>
      certfile: <cert-path>

  resources:
    ports: 8080

  run: python3 -m http.server 8080

To deploy the service, run the following command:

.. code-block:: bash

  $ sky serve up https.yaml -n my-service

If you are using a self-signed certificate, you may need to add the :code:`-k` flag to the :code:`curl` command to bypass certificate:

.. code-block:: bash
  :emphasize-lines: 2

  $ ENDPOINT=$(sky serve status --endpoint my-service)
  $ curl -k $ENDPOINT
  <!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">
  <html>
  <head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <title>Directory listing for /?</title>
  </head>
  <body>
  <h1>Directory listing for /?</h1>
  <hr>
  <ul>
  </ul>
  <hr>
  </body>
  </html>

If you are using a wrong schema (HTTP instead of HTTPS), or does not have the :code:`-k` flag for a self-signed certificate, you will see an error message:

.. code-block:: bash

  $ curl $ENDPOINT
  curl: (60) SSL certificate problem: self signed certificate
  More details here: https://curl.se/docs/sslcerts.html

  curl failed to verify the legitimacy of the server and therefore could not
  establish a secure connection to it. To learn more about this situation and
  how to fix it, please visit the web page mentioned above.
  $ curl "${ENDPOINT/https:/http:}"
  curl: (52) Empty reply from server
