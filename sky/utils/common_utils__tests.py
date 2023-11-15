from sky.utils.common_utils import _adjust_cluster_name


def test_adjust_cluster_name_with_uppercase_letters():
    assert _adjust_cluster_name("LoRA") == "lora"


def test_adjust_cluster_name_with_underscore():
    assert _adjust_cluster_name("seed_1") == "seed1"


def test_adjust_cluster_name_with_period():
    assert _adjust_cluster_name("cuda11.8") == "cuda118"


def test_adjust_cluster_names_starting_with_number():
    assert _adjust_cluster_name("2bert") == "x2bert"


def test_adjust_cluster_name_with_multiple_invalid_characters():
    assert _adjust_cluster_name("2Lo_R.A") == "x2lora"
