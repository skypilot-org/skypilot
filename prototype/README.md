# Sky Prototype

## Setup

0. `pip install -e .`

1. Install Airflow:

    ```
    AIRFLOW_VERSION=2.1.4
    PYTHON_VERSION="$(python --version | cut -d " " -f 2 | cut -d "." -f 1-2)"
    CONSTRAINT_URL="https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"
    pip install "apache-airflow==${AIRFLOW_VERSION}" --constraint "${CONSTRAINT_URL}"
    ```

2. `pip install -r requirements.txt`

3. Initialize Airflow

    ```
    airflow db init
      
    airflow users create \
        --username admin \
        --firstname Peter \
        --lastname Parker \
        --role Admin \
        --email spiderman@superhero.org
    ```

4. Config Airflow: open `~/airflow/airflow.cfg`. On line 841, set `dag_dir_list_interval = 1`.

5. Run Airflow

    ```
    airflow webserver --port 8080
    airflow scheduler
    ```

6. Run `python main.py`, which will submit your job to Airflow and open http://localhost:8080/dagrun/list/ for monitoring progress.
