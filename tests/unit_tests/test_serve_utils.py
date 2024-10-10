import pytest

from sky.serve import serve_utils


class TestHasValidReplicaId:

    def test_has_valid_replica_id_valid(self):
        assert serve_utils.has_valid_replica_id('replica_1.log', 1) is True

    def test_has_valid_replica_id_invalid(self):
        assert serve_utils.has_valid_replica_id('replica_2.log', 1) is False

    def test_has_valid_replica_id_none(self):
        assert serve_utils.has_valid_replica_id('replica_1.log', None) is True

    def test_has_valid_replica_id_no_match(self):
        with pytest.raises(
                ValueError,
                match=
                'Failed to get replica id from file name: invalid_prefix.log'):
            serve_utils.has_valid_replica_id('invalid_prefix.log', 1)

    def test_has_valid_replica_id_invalid_suffix(self):
        with pytest.raises(
                ValueError,
                match=
                'Failed to get replica id from file name: replica_1.invalid_suffix'
        ):
            serve_utils.has_valid_replica_id('replica_1.invalid_suffix', 1)
