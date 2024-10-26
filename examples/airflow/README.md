# SkyPilot Airflow integration examples

This directory contains two examples of integrating SkyPilot with Apache Airflow:
1. [training_workflow](training_workflow)
    * A simple training workflow that preprocesses data, trains a model, and evaluates it.
    * Showcases how SkyPilot can help easily transition from dev to production in Airflow.
2. [shared_state](shared_state)
    * An example showing how SkyPilot state can be persisted across Airflow tasks.
    * Useful for operating on the same shared SkyPilot clusters from different Airflow tasks.