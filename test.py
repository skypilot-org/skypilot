import functools


@functools.lru_cache(maxsize=1)
def test(x):
    print(f'test {x}')
    return x


f = test
test(1)
