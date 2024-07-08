from unittest.mock import patch

import pytest

from sky.clouds.aws import AWS


def test_aws_label():
    aws = AWS()
    # Invalid - AWS prefix
    assert aws.is_label_valid("aws:whatever", "value")[0] == False
    # Valid - valid prefix
    assert aws.is_label_valid("any:whatever", "value")[0] == True
    # Invalid - Too long
    assert (aws.is_label_valid(
        "sprinto:thisiexample_string_with_123_characters_length_thing_thing_thing_thing_thing_thing_thing_thin_thing_thing_thing_thing_thing_thing",
        "value",
    )[0] == False)
    # Invalid - Too long
    assert (aws.is_label_valid(
        "sprinto:short",
        "thisiexample_string_with_123_characters_length_thing_thing_thing_thing_thing_thing_thing_thin_thing_thing_thing_thing_thing_thingthisiexample_string_with_123_characters_length_thing_thing_thing_thing_thing_thing_thing_thin_thing_thing_thing_thing_thing_thing",
    )[0] == False)
