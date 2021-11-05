from absl import app
from absl import flags
from absl import logging
import boto3
import numpy as np
import pandas as pd
import ray

REGIONS = ["us-west-1", "us-west-2", "us-east-1", "us-east-2"]
PRICING_TABLE_URL_FMT = "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/{region}/index.csv"


@ray.remote
def get_instance_types(region: str) -> pd.DataFrame:
    client = boto3.client("ec2", region_name=region)
    paginator = client.get_paginator("describe_instance_types")
    items = []
    for i, resp in enumerate(paginator.paginate()):
        print(f"{region} getting instance types page {i}")
        items += resp["InstanceTypes"]
    return pd.DataFrame(items)


@ray.remote
def get_pricing_table(region: str) -> pd.DataFrame:
    print(f"{region} downloading pricing table")
    url = PRICING_TABLE_URL_FMT.format(region=region)
    df = pd.read_csv(url, skiprows=5, low_memory=False)
    return df[(df["TermType"] == "OnDemand") &
              (df["Operating System"] == "Linux") &
              df["Pre Installed S/W"].isnull() &
              (df["CapacityStatus"] == "Used") &
              (df["Tenancy"].isin(["Host", "Shared"])) &
              df["PricePerUnit"] > 0].set_index("Instance Type")


@ray.remote
def get_instance_types_df(region: str) -> pd.DataFrame:
    df, pricing_df = ray.get(
        [get_instance_types.remote(region),
         get_pricing_table.remote(region)])

    def get_price(row):
        t = row["InstanceType"]
        try:
            return pricing_df.loc[t]["PricePerUnit"]
        except KeyError:
            print(f"{region} WARNING: cannot find pricing for {t}")
            return np.nan

    df["PricePerHour"] = df.apply(get_price, axis=1)
    df["Region"] = region
    return df


def get_all_regions_instance_types_df():
    dfs = ray.get([get_instance_types_df.remote(r) for r in REGIONS])
    df = pd.concat(dfs)
    df.sort_values(["InstanceType", "Region"], inplace=True)
    return df


def main(argv):
    del argv  # Unused.
    ray.init()
    logging.set_verbosity(logging.DEBUG)
    df = get_all_regions_instance_types_df()
    df.to_csv("aws.csv", index=False)
    print("AWS Service Catalogue saved to aws.csv")


if __name__ == "__main__":
    app.run(main)
