from sky.utils.common_utils import _adjust_cluster_name


def test_adjust_cluster_names_with_uppercase_letters():
    assert _adjust_cluster_name("LoRA") == "lora"
    assert _adjust_cluster_name("Bert") == "bert"


def test_adjust_cluster_names_with_underscores():
    assert _adjust_cluster_name("seed_1") == "seed1"


def test_adjust_cluster_names_with_periods():
    assert _adjust_cluster_name("cuda11.8") == "cuda118"
