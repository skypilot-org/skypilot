"""Script to get the default security group"""
from sky.clouds import aws


def main():
    default_security_group = aws.DEFAULT_SECURITY_GROUP_NAME
    print(f'{default_security_group}')


if __name__ == '__main__':
    main()
