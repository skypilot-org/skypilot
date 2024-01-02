import math

import pulp
from pulp import LpInteger
from pulp import LpMinimize
from pulp import LpProblem
from pulp import LpVariable

from sky.serve.serve_utils import AcceleratorType


# `request_distribution` is a list of length 7 where each element is
# the request rate (in req/s) for its corresponding request size
# histogram bucket. This is assuming the LLM bucket boundaries.
def IlpSolver(request_rate_histogram):
    problem = LpProblem("GpuAllocation", LpMinimize)

    gpu_types = [AcceleratorType.A10, AcceleratorType.A100]

    # A10, A100 hourly prices
    cost_vector = [1.01, 3.67]

    # A10, A100 normalized capacity
    capacity_vector = [1.32, 4.74]

    # A10, A100 normalization coefficients
    norm_coefficients = [
        [0.3317, 0.4415, 0.6316, 1, 1.7143, 3.1429, 5.28],  # A10
        [0.4566, 0.4852, 0.6253, 1, 1.5748, 2.7558, 4.514]  # A100
    ]
    assert len(cost_vector) == len(capacity_vector) == len(norm_coefficients)
    assert len(request_rate_histogram) == len(norm_coefficients[0])

    # Matrix value is 0/1 slice assignment
    matrix_rows = len(norm_coefficients[0])  # Each row is for a single slice
    matrix_cols = len(norm_coefficients)  # Each column is a GPU type

    # Vector value is non-negative integer of how many of each GPU type are needed
    vector_length = matrix_cols  # Each element in vector corresponds to GPU type's num GPUs

    decision_matrix = [[
        LpVariable(f"x_{i}_{j}", cat=LpInteger, lowBound=0, upBound=1)
        for j in range(matrix_cols)
    ]
                       for i in range(matrix_rows)]

    decision_vector = [
        LpVariable(f"y_{i}", cat=LpInteger, lowBound=0)
        for i in range(vector_length)
    ]

    # Objective: minimize cost
    problem += pulp.lpSum([
        decision_vector[i] * cost_vector[i] for i in range(len(decision_vector))
    ])

    # C1: Each row of decision matrix must sum to exactly 1 (ie, each slice assigned to one GPU)
    for i in range(len(decision_matrix)):
        problem += pulp.lpSum(decision_matrix[i]) == 1

    # C2: Load of column of decision matrix must fit in decision vector capacity
    for j in range(len(decision_matrix[0])):
        # j is idx of GPU type, i is slice
        problem += pulp.lpSum([
            decision_matrix[i][j] * norm_coefficients[j][i] *
            request_rate_histogram[i] for i in range(len(decision_matrix))
        ]) / capacity_vector[j] <= decision_vector[j]

    # C3: There must always be at least one allocated instance.
    problem += pulp.lpSum(decision_vector) >= 1

    # Solve the problem
    # problem.solve(pulp.COIN_CMD(path='/opt/homebrew/opt/cbc/bin/cbc', msg=0))
    problem.solve(pulp.PULP_CBC_CMD(msg=0))

    # Print the results
    # print("Status:", pulp.LpStatus[problem.status])
    # print(f'Decision Matrix:')
    # for row in decision_matrix:
    #     print([var.value() for var in row])
    # print(f'Decision Vector:')
    # print(f'{[var.value() for var in decision_vector]}')

    if pulp.LpStatus[problem.status] == 'Optimal':
        # Holds number of each GPU type needed
        solution_dict = {}
        for i in range(len(decision_vector)):
            solution_dict[gpu_types[i]] = decision_vector[i].value()

        # Holds mapping from request size to GPU type
        assignment_vector = []
        for i in range(len(decision_matrix)):
            for j in range(len(decision_matrix[i])):
                if decision_matrix[i][j].value() == 1:
                    assignment_vector.append(gpu_types[j])
                    break
        
        return solution_dict, assignment_vector

    return None, None


# `request_distribution`: List[int], where integer r at index i corresponds to the
#                         observed ratio of total requests that is represented by
#                         requests in this bucket. For now, we assume we know the
#                         bucket boundaries
# `request_rate`: float, which is the total observed (or predicted) request rate
# `profiling`: Dict{}, which holds the statistics derived offline by the profiler
def OptimalAllocation_BruteForce(request_distribution, request_rate, profiling):
    for gpu in profiling:
        assert (len(request_distribution) == len(gpu["norm_coefficients"]))

    request_rates = [ratio * request_rate for ratio in request_distribution]

    # Compute maximum number of GPUs necessary
    # Currently assumes only A10 and A100. TODO: remove this assumption.  normalized_loads = {}
    a10_normalized_load = sum([
        request_rates[i] * profiling["A10"]["norm_coefficients"][i]
        for i in range(len(request_rates))
    ])
    a100_normalized_load = sum([
        request_rates[i] * profiling["A100"]["norm_coefficients"][i]
        for i in range(len(request_rates))
    ])

    max_gpus = max(
        math.ceil(a10_normalized_load / profiling["A10"]["capacity"]),
        math.ceil(a100_normalized_load / profiling["A100"]["capacity"]),
    )

    # Brute force all GPU combinations
    # (lowest cost, num A100s, num A10s)
    cur_allocation = (float('inf'), 0, 0)

    for total_gpus in range(1, max_gpus + 1):
        for num_a10s in range(0, total_gpus + 1):
            num_a100s = total_gpus - num_a10s
            remaining_load = request_rates.copy()

            # Compute the load that A100 can handle
            if num_a100s > 0:
                a100_capacity = profiling['A100']['capacity'] * num_a100s
                for bucket_idx in range(len(remaining_load) - 1, -1, -1):
                    bucket_load = profiling["A100"]["norm_coefficients"][
                        bucket_idx] * remaining_load[bucket_idx]
                    if a100_capacity > bucket_load:
                        a100_capacity -= bucket_load
                        remaining_load[bucket_idx] = 0
                    else:
                        fractional_load = a100_capacity / bucket_load
                        remaining_load[
                            bucket_idx] -= fractional_load * remaining_load[
                                bucket_idx]
                        # A100 can't handle any more load
                        break

            # Try to assign A10 the remaining load
            a10_capacity = profiling['A10']['capacity'] * num_a10s
            total_load = sum([
                remaining_load[i] * profiling["A10"]["norm_coefficients"][i]
                for i in range(len(remaining_load))
            ])
            if a10_capacity >= total_load:
                # Success!
                cost = num_a100s * profiling['A100'][
                    'price'] + num_a10s * profiling['A10']['price']
                if cost < cur_allocation[0]:
                    cur_allocation = (cost, num_a100s, num_a10s)

    return cur_allocation
