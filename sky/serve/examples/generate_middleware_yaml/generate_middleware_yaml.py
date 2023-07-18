import os
import jinja2
from sky.utils import common_utils
# from sky.backends import backend_utils

service_id = 'somerandomcharacters'
original_yaml_path = '../http_server/task.yaml'
middleware_yaml_template_path = 'middleware.yaml.j2'

modified_yaml_path = 'task_in_middleware.yaml'
middleware_yaml_path = 'middleware.yaml'

original = common_utils.read_yaml(original_yaml_path)
original_workdir = original.get('workdir', None)

workdir_abs_path = None
if original_workdir is not None:
    # Upload the workdir to middleware:~/sky_workdir
    # Then upload middleware:~/sky_workdir to the endpoint replica
    workdir_path = os.path.join(os.path.dirname(original_yaml_path), original_workdir)
    workdir_abs_path = os.path.abspath(workdir_path)
    original['workdir'] = '~/sky_workdir'
# TODO(tian): update file_mounts as well

service = original.get('service', None)
assert service is not None
assert 'port' in service
if 'resources' in original:
    # TODO(tian): Maybe ignore any ports specified in the original resoureces?
    if 'ports' in original['resources']:
        assert len(original['resources']['ports']) == 1 and original['resources']['ports'][0] == service['port']
common_utils.dump_yaml(modified_yaml_path, original)

variables = {
    'port': service['port'],
    'workdir': workdir_abs_path,
    'service_id': service_id,
    'modified_yaml_path': modified_yaml_path,
}

# TODO(tian): change to backend_utils.fill_template
# backend_utils.fill_template(middleware_yaml_template_path,
#                             variables,
#                             output_path=middleware_yaml_path)

with open(middleware_yaml_template_path) as fin:
    template = fin.read()
output_path = os.path.abspath(os.path.expanduser(middleware_yaml_path))
os.makedirs(os.path.dirname(output_path), exist_ok=True)

# Write out yaml config.
j2_template = jinja2.Template(template)
content = j2_template.render(**variables)
with open(output_path, 'w') as fout:
    fout.write(content)
