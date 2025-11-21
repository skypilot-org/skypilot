import sky

cluster_name = 'localgpt'
task = sky.Task.from_yaml('localgpt.yaml')
request_id = sky.launch(task,
                        cluster_name=cluster_name,
                        _need_confirmation=True)
job_id, _ = sky.stream_and_get(request_id)

logs = sky.tail_logs(cluster_name, job_id, follow=True, preload_content=False)
found_logline = False
for log in logs:
    print(log, end='')
    if "INFO:werkzeug:" in log and "Press CTRL+C to quit" in log:
        found_logline = True
        break
logs.close()

if found_logline:
    print("Cluster launched. In a new terminal run:\n\n"
          f"ssh -L 5111:localhost:5111 {cluster_name}\n\n"
          "while keeping this terminal running, open \n\n"
          "http://localhost:5111\n\n"
          "in your browser to start using localGPT")
else:
    print("Cluster failed to launch")
