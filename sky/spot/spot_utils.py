import shlex

class SpotCodeGen:
    _PREFIX = [
        'from sky.spot import spot_status'
    ]
    def __init__(self):
        self._code = []

    def show_jobs(self) -> str:
        self._code += [
            'job_table = spot_status.show_jobs()',
            'print(job_table)'
        ]
        return self._build()
    
    def _build(self):
        code = self._PREFIX + self._code
        code = '; '.join(code)
        return f'python3 -u -c {shlex.quote(code)}'
