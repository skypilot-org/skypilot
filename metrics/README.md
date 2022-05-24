# Dev Guide for Metrics 

## Instrumentation

To instrument your code, start by adding a `MetricLogger` for function you want to log. This class and it's documentation can be found in `sky.utils.metrics`.

Example MetricLogger:
```
test_logger = metrics.MetricLogger(
    'test_func_name', 
    labels=[metrics.Label('cluster_name'),
                    metrics.Label('yaml_info')],
    metrics=[metrics.Metric('retries')],
    with_runtime = True)
```

Commonly used metrics (runtime) and labels (collecting CLI command) are implemented already and can be activated specifiying `with_runtime` or `with_cmd`.

Following this, you must add the decorator, i.e adding `@test_logger.decorator` as a decorator to the intended function. Then during the function, you can 
set metrics, labels, and the return code. For example

```
@test_logger.decorator
def test_func(cluster_name, yaml_info)
  retries = 0
  test_logger.set_labels({'cluster_name': cluster_name, 'yaml_info': yaml_info})
  while True:
    if do_something(): break
    retries += 1
  test_logger.set_metrics({'retries': retries})
  if failed():
    test_logger.set_return_code(1)
```
   


## Accessing Metrics

To access the metrics directly you can access the following links:

Prometheus Pushgate: http://3.216.190.117:9091/
Prometheus: http://3.216.190.117:9090/

The Grafana dashboard can also be accessed as outlined in [this document](https://docs.google.com/document/d/1MvwOtxl00OzWfuB0N5S1xXUbs7QvLUzbj9ycQAqNz6g/edit)
