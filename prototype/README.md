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

4. Copy the Sky DAG to Airflow directory: `mkdir -p ~/airflow/dags && cp sky_dag.py ~/airflow/dags/`

    1. (I'm working to remove this step)

5. Run `python main.py`

6. Navigate to http://localhost:8080/dagrun/list/ to see your run

