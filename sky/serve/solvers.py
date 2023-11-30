import math


# `request_distribution`: List[int], where integer r at index i corresponds to the
#                         observed ratio of total requests that is represented by
#                         requests in this bucket. For now, we assume we know the 
#                         bucket boundaries
# `request_rate`: float, which is the total observed (or predicted) request rate
# `profiling`: Dict{}, which holds the statistics derived offline by the profiler
def OptimalAllocation_BruteForce(request_distribution, request_rate, profiling):
  for gpu in profiling:
    assert(len(request_distribution) == len(gpu["norm_coefficients"]))

  request_rates = [ratio * request_rate for ratio in request_distribution]

  # Compute maximum number of GPUs necessary
  # Currently assumes only A10 and A100. TODO: remove this assumption.  normalized_loads = {}
  a10_normalized_load = sum([request_rates[i] * profiling["A10"]["norm_coefficients"][i] for i in range(len(request_rates))])
  a100_normalized_load = sum([request_rates[i] * profiling["A100"]["norm_coefficients"][i] for i in range(len(request_rates))])

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
          a100_capacity = profiling['A100']['capacity'] *  num_a100s
          for bucket_idx in range(len(remaining_load) - 1, -1, -1):
            bucket_load = profiling["A100"]["norm_coefficients"][bucket_idx] * remaining_load[bucket_idx]
            if a100_capacity > bucket_load:
              a100_capacity -= bucket_load
              remaining_load[bucket_idx] = 0
            else:
              fractional_load = a100_capacity / bucket_load
              remaining_load[bucket_idx] -= fractional_load * remaining_load[bucket_idx]
              # A100 can't handle any more load
              break
        
        # Try to assign A10 the remaining load
        a10_capacity = profiling['A10']['capacity'] *  num_a10s
        total_load = sum([remaining_load[i] * profiling["A10"]["norm_coefficients"][i] for i in range(len(remaining_load))])
        if a10_capacity >= total_load:
          # Success!
          cost = num_a100s * profiling['A100']['price'] + num_a10s * profiling['A10']['price']
          if cost < cur_allocation[0]:
            cur_allocation = (cost, num_a100s, num_a10s)
  
  return cur_allocation