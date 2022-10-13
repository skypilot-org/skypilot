"""Echo app

Reads a file, echoes it and writes back to a specified path.
"""
import argparse


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Echo app')
    parser.add_argument('input', type=str)
    parser.add_argument('output', type=str)
    args = parser.parse_args()

    with open(args.input, 'r') as input_file:
        content = input_file.read()
    print("===== echo app =====")
    print("Input file content:")
    print(content)
    with open(args.output, 'w') as output_file:
        output_file.write(content)
    print("Output written to {}".format(args.output))


if __name__ == '__main__':
    main()
