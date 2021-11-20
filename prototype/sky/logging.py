import io
import os
import subprocess
import sys
import logging
import selectors

FORMAT = '%(levelname).1s %(asctime)s %(filename)s:%(lineno)d] %(message)s'
DATE_FORMAT = '%m-%d %H:%M:%S'


class NewLineFormatter(logging.Formatter):

    def __init__(self, fmt, datefmt=None):
        logging.Formatter.__init__(self, fmt, datefmt)

    def format(self, record):
        msg = logging.Formatter.format(self, record)
        if record.message != '':
            parts = msg.split(record.message)
            msg = msg.replace('\n', '\n' + parts[0])
        return msg


def init_logger(name):
    h = logging.StreamHandler(sys.stdout)
    h.flush = sys.stdout.flush

    fmt = NewLineFormatter(FORMAT, datefmt=DATE_FORMAT)
    h.setFormatter(fmt)

    logger = logging.getLogger(name)
    logger.addHandler(h)
    logger.setLevel(logging.DEBUG)
    return logger

def subprocess_output(proc, 
                    out_path, 
                    stream_logs, 
                    start_streaming_at = ''):
    dirname = os.path.dirname(out_path)
    os.makedirs(dirname, exist_ok=True)
    
    out_io = io.TextIOWrapper(proc.stdout,
                                encoding='utf-8',
                                newline='')
    err_io = io.TextIOWrapper(proc.stderr,
                                encoding='utf-8',
                                newline='')
    sel = selectors.DefaultSelector()
    sel.register(out_io, selectors.EVENT_READ)
    sel.register(err_io, selectors.EVENT_READ)
    
    stdout = ''
    stderr = ''
    
    start_streaming_flag = False
    with open(out_path, 'w') as fout:
        while True:
            for key, _ in sel.select():
                line = key.fileobj.readline()
                if not line:
                    return stdout, stderr
                if start_streaming_at in line:
                    start_streaming_flag = True
                if key.fileobj is out_io:
                    stdout += line
                    fout.write(line)
                    fout.flush()
                else:
                    stderr += line
                    fout.write(line)
                    fout.flush()
                if stream_logs and start_streaming_flag:
                    print(line, end='')
    