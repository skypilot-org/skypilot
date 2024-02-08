"""Tests for methods in common_utils.py"""
from sky.utils.common_utils import adjust_cluster_name


class TestAdjustClusterName:

    def test_adjust_cluster_name_with_uppercase_letters(self):
        assert adjust_cluster_name("LoRA") == "lora"

    def test_adjust_cluster_name_with_underscore(self):
        assert adjust_cluster_name("seed_1") == "seed-1"

    def test_adjust_cluster_name_with_period(self):
        assert adjust_cluster_name("cuda11-8") == "cuda11-8"

    def test_adjust_cluster_names_starting_with_number(self):
        assert adjust_cluster_name("2bert") == "x2bert"

    def test_adjust_cluster_name_with_multiple_invalid_characters(self):
        assert adjust_cluster_name("2Lo_R.A") == "x2lo-r-a"
