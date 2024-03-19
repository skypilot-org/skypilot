"""Cudo Compute VM spec helper for SkyPilot."""
import csv

from sky.clouds.service_catalog.common import get_catalog_path

VMS_CSV = 'cudo/vms.csv'


def get_spec_from_instance(instance_type, data_center_id):
    path = get_catalog_path(VMS_CSV)
    spec = []
    with open(path, mode='r', encoding='utf-8') as file:
        csv_reader = csv.reader(file)
        for row in csv_reader:
            if row and row[0] == instance_type and row[6] == data_center_id:
                spec = row
                break
    return {
        'gpu_model': spec[1],
        'vcpu_count': spec[3],
        'mem_gb': spec[4],
        'gpu_count': spec[2],
        'machine_type': spec[0].split('_')[0]
    }
