"""A script that generates the Vast Cloud catalog. """
# pylint: disable=assignment-from-no-return
import csv
import os

from sky.adaptors import vast
from sky.catalog.vast import _offer_processing as proc

# Vast has a wide variety of machines, some of which will have less diskspace
# and network bandwidth than others. The flags here mirror the historical
# behavior of this fetcher:
#   * georegion consolidates geographic areas
#   * chunked rounds down specifications (e.g. 1025GB to 1024GB disk) so
#     machine specifications look consistent
#   * inet_down keeps machines with reasonable downlink speed
#   * disk_space sets a lower bound to filter tiny disk pools
_QUERY = ('georegion = true chunked = true '
          'inet_down >= 100 disk_space >= 80')

if __name__ == '__main__':
    offer_list = vast.vast().search_offers(query=_QUERY, limit=10000)

    rows = proc.build_csv_rows(offer_list)

    os.makedirs('vast', exist_ok=True)
    with open('vast/vms.csv', 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=proc.CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
