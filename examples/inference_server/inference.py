import os
import sys

INFERENCE_RESULT_MARKER = "INFERENCE RESULT:"


def run_inference(image_path):
    # Perform some computation on the image located at image_path

    # Instead of returning the result,
    # print it to stdout so that the server can retrieve the result from the logs
    print(
        f"{INFERENCE_RESULT_MARKER}Ran inference on the image at '{image_path}' with size {os.path.getsize(image_path)}B."
    )


if __name__ == "__main__":
    run_inference(sys.argv[1])
