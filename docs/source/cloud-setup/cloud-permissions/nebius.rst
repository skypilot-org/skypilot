Nebius
======

.. _nebius-service-account:

Service account
----------------

To use *Service Account* authentication, follow these steps:

1. **Create a Service Account** using the Nebius web console.
2. **Generate PEM Keys**:

.. code-block:: shell

   openssl genrsa -out private.pem 4096
   openssl rsa -in private.pem -outform PEM -pubout -out public.pem

3.  **Generate and Save the Credentials File**:

* Save the file as `~/.nebius/credentials.json`.
* Ensure the file matches the expected format below:

.. code-block:: json

     {
         "subject-credentials": {
             "alg": "RS256",
             "private-key": "PKCS#8 PEM with new lines escaped as \n",
             "kid": "public-key-id",
             "iss": "service-account-id",
             "sub": "service-account-id"
         }
     }


**Important Notes:**

* The `NEBIUS_IAM_TOKEN` file, if present, will take priority for authentication.
* Service Accounts are restricted to a single region. Ensure you configure the Service Account for the appropriate region during creation.
