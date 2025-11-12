import ray

ray.init()


@ray.remote
def square(x):
    return x * x


results = ray.get([square.remote(i) for i in range(100)])
print(f'Sum of squares: {sum(results)}')

ray.shutdown()
