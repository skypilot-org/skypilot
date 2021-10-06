# Sky Execution Engine Prototype

## Setup

1. `pip install -r requirements.txt`

2. Initialize Airflow

    ```
    airflow db init
      
    airflow users create \
        --username admin \
        --firstname Peter \
        --lastname Parker \
        --role Admin \
        --email spiderman@superhero.org
    ```

3. Run Airflow

    ```
    airflow webserver --port 8080
    airflow scheduler
    ```

4. Run `python main.py`, which will submit your job to Airflow and open http://localhost:8080/dagrun/list/ for monitoring progress.
