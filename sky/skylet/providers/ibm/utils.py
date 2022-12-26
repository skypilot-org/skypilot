import logging
import os
import time

logger = logging.getLogger(__name__)


def get_logger():
    """
    Configures the logger of this module for console output and file output
    logs of level DEBUG and higher will be directed to file under LOGS_FOLDER.
    logs of level INFO and higher will be directed to console output. 
    """
    LOGS_FOLDER = "/tmp/connector_logs/"   # this node_provider's logs location. 
    logger.setLevel(logging.DEBUG)

    if not os.path.exists(LOGS_FOLDER):
        os.mkdir(LOGS_FOLDER)
    logs_path =  LOGS_FOLDER + time.strftime("%Y-%m-%d--%H-%M-%S")

    file_formatter = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    file_handler = logging.FileHandler(logs_path)
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)

    console_output_handler = logging.StreamHandler()
    console_output_handler.setFormatter(file_formatter)
    console_output_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_output_handler)    
    return logger