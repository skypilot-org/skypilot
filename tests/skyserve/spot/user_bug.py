import time

# The program exits to simulate a user app bug.
if __name__ == "__main__":
    time.sleep(30)
    assert False
