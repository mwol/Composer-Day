# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
https://airflow.apache.org/docs/apache-airflow/stable/concepts/variables.html
* gcp_project - the project id used
* email - The email used to receive DAG updates.
"""

import datetime
from airflow import models
from airflow.models.param import Param
from airflow.operators import bash, email, python_operator
from airflow.providers.google.cloud.operators import bigquery
from airflow.providers.google.cloud.transfers import bigquery_to_gcs
from airflow.utils import trigger_rule


target_dataset_name = "greenhat_summary"
target_table_name = "readings_by_street"
location = "us-central1"
project_id = "composer-workshop"
gcs_bucket = "{{params.output_gcs_bucket}}"
csv_output_file = f"gs://{gcs_bucket}/street_readings.csv"
avro_output_file = f"gs://{gcs_bucket}/street_readings.avro"

# Data from the month of January 2018
# You may change the query dates to get data from a different time range. You
# may also dynamically pick a date range based on DAG schedule date. Airflow
# macros can be useful for this. For example, {{ macros.ds_add(ds, -7) }}
# corresponds to a date one week (7 days) before the DAG was run.
# https://airflow.apache.org/code.html?highlight=execution_date#airflow.macros.ds_add
max_query_date = "2022-03-01"
min_query_date = "2022-01-01"

BQ_AGG_STREETS_QUERY = """
        SELECT 
            roads.osm_name AS road_name,
            AVG(readings.co_ppm) AS avg_co_ppm,
            MIN(readings.timestamp) AS min_timestamp,
            MAX(readings.timestamp) AS max_timestamp,
        FROM `composer-workshop.greenhat.readings` AS readings
        LEFT JOIN `composer-workshop.greenhat.space_and_time_continuum` AS space_and_time
            ON readings.timestamp = space_and_time.timestamp
        LEFT JOIN `composer-workshop.greenhat.road_config` AS roads
            ON space_and_time.road_id = roads.road_id
        WHERE readings.timestamp >= CAST('{{ params.min_query_date }}' AS TIMESTAMP)
            AND readings.timestamp < CAST('{{ params.max_query_date }}' AS TIMESTAMP)
        GROUP BY road_name
        LIMIT 1000
        """

default_business_datetime = (datetime.datetime.utcnow() - datetime.timedelta(days=1)).strftime("%Y_%m_%d_%H_%M")

default_dag_args = {
    "start_date": datetime.datetime(2023, 10, 1),
    "email": "{{var.value.email}}",
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": datetime.timedelta(minutes=5),
    "project_id": project_id,
}

with models.DAG(
    "first_dag",
    schedule_interval=datetime.timedelta(weeks=4),
    default_args=default_dag_args,
    params={
         "max_query_date": Param("2022-03-01", type="string"),
         "min_query_date": Param("2022-01-01", type="string"),
         "bq_dataset_name": Param("greenhat_summary", type="string"),
         "business_datetime": Param(default_business_datetime, type="string"),
         "gcp_project": Param("composer-workshop", type="string"),
         "output_gcs_bucket": Param("composer-workshop-data-output", type="string")
     },
) as dag:
    def greeting():
        import logging
        logging.info("Goodbye!")

    # Create BigQuery output dataset.
    make_bq_dataset = bash.BashOperator(
        task_id="make_bq_dataset",
        # Executing 'bq' command requires Google Cloud SDK which comes
        # preinstalled in Cloud Composer.
        bash_command="bq ls  {{ params.bq_dataset_name }} || bq mk --location=us-central1 {{ params.bq_dataset_name }}",
    )
    
    bq_aggregate_streets = bigquery.BigQueryInsertJobOperator(
        task_id="bq_aggregate_streets",
        configuration={
            "query": {
                "query": BQ_AGG_STREETS_QUERY,
                "useLegacySql": False,
                "destinationTable": {
                    "projectId": project_id,
                    "datasetId": target_dataset_name,
                    "tableId": target_table_name + "_{{ params.business_datetime }}",
                },
            }
        },
        location=location,
    )

    aggregate_streets_to_csv = bigquery_to_gcs.BigQueryToGCSOperator(
        task_id="aggregate_streets_to_csv",
        source_project_dataset_table=f"{project_id}.{target_dataset_name}.{target_table_name}_{{{{ params.business_datetime }}}}",
        destination_cloud_storage_uris=[csv_output_file],
        export_format="CSV", 
    )

    aggregate_streets_to_avro = bigquery_to_gcs.BigQueryToGCSOperator(
        task_id="aggregate_streets_to_avro",
        source_project_dataset_table=f"{project_id}.{target_dataset_name}.{target_table_name}_{{{{ params.business_datetime }}}}",
        destination_cloud_storage_uris=[avro_output_file],
        export_format="AVRO",
    )

    this_is_the_end = python_operator.PythonOperator(
        task_id="goodbye", 
        provide_context=True,
        python_callable=greeting,
        op_kwargs={'name': '{{ airflow-variables-maurice-secret-01 }}'},
    )


    # Define DAG dependencies.
    make_bq_dataset >> bq_aggregate_streets
    bq_aggregate_streets >> aggregate_streets_to_avro
    bq_aggregate_streets >> aggregate_streets_to_csv
    aggregate_streets_to_avro >> this_is_the_end
    aggregate_streets_to_csv >> this_is_the_end
    