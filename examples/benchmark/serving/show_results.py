import click
import textwrap

from sky.benchmark import benchmark_state
from sky.benchmark import benchmark_utils
from sky.utils import log_utils

@click.command()
@click.argument('benchmark', required=True, type=str)
@click.option('--batch-size', default=5, required=False, type=int, help='Number of queries per batch.')
def show_results(benchmark: str, batch_size: int) -> None:
    """Show serving benchmark report."""
    record = benchmark_state.get_benchmark_from_name(benchmark)
    if record is None:
        raise click.BadParameter(f'Benchmark {benchmark} does not exist.')
    benchmark_utils.update_benchmark_state(benchmark)

    click.echo(
        textwrap.dedent("""\
        Legend:
        - #QUERIES: Number of queries sent in the benchmark.
        - SEC/QUERY, $/QUERY: Average time and cost per query.
    """))
    columns = [
        'RESOURCES',
        '#QUERIES',
        'SEC/QUERY',
        '$/QUERY',
    ]

    cluster_table = log_utils.create_table(columns)
    rows = []
    benchmark_results = benchmark_state.get_benchmark_results(benchmark)
    for result in benchmark_results:
        num_nodes = result['num_nodes']
        resources = result['resources']
        if resources.accelerators:
            gpu = list(resources.accelerators.keys())[0]
            count = resources.accelerators[gpu]
            resources_str = f'{gpu}:{count}'
        else:
            resources_str = f'CPU:{resources.cpus}'

        record = result['record']
        if (record is None or record.start_time is None or
                record.last_time is None):
            row += ['-'] * (len(columns) - len(row))
            rows.append(row)
            continue

        num_steps = record.num_steps_so_far
        if num_steps is None:
            num_queries = 0
        else:
            num_queries = num_steps * batch_size

        seconds_per_step = record.seconds_per_step
        if seconds_per_step is None:
            seconds_per_query_str = '-'
            cost_per_query_str = '-'
        else:

            seconds_per_query_str = f'{seconds_per_step/batch_size:.4f}'
            cost_per_step = num_nodes * resources.get_cost(seconds_per_step)
            cost_per_query_str = f'{cost_per_step/batch_size:.6f}'

        row = [
            # RESOURCES
            f'{num_nodes}x {resources_str}',
            # #QUERIES
            num_queries,
            # SEC/QUERY
            seconds_per_query_str,
            # $/QUERY
            cost_per_query_str,
        ]
        rows.append(row)

    cluster_table.add_rows(rows)
    click.echo(cluster_table)


if __name__ == '__main__':
    show_results()