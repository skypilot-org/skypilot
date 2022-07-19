import sys

INFERENCE_RESULT_MARKER = "INFERENCE RESULT:"


def run_inference(input):
    # Perform some computation on input here

    # Instead of returning the result,
    # print it to stdout so that the server can retrieve the result from the logs
    print(
        f"{INFERENCE_RESULT_MARKER}This is the result of running inference on '{input}'"
    )


if __name__ == "__main__":
    run_inference(sys.argv[1])
