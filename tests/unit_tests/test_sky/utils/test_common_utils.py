from sky.utils import common_utils

# Test for the truncate_long_string function
def test_truncate_long_string():
    # Test normal cases (no truncation)
    assert common_utils.truncate_long_string('short', 10) == 'short'
    assert common_utils.truncate_long_string('', 10) == ''
    
    # Test end truncation (default)
    assert common_utils.truncate_long_string(
        'this is a very long string', 10) == 'this is a ...'
    
    # Test middle truncation
    assert common_utils.truncate_long_string(
        'abcdefghijklmnopqrstuvwxyz', 10, truncate_middle=True) == 'abcd...xyz'
    
    # Test odd max_length
    assert common_utils.truncate_long_string(
        'abcdefghijklmnopqrstuvwxyz', 11, truncate_middle=True) == 'abcd...wxyz'
    
    # Test very short max_length
    assert common_utils.truncate_long_string(
        'abcdefghijklmnopqrstuvwxyz', 3, truncate_middle=True) == '...'
    
    # Test max_length equal to string length
    assert common_utils.truncate_long_string(
        'abcde', 5, truncate_middle=True) == 'abcde'
    
    # Test max_length one less than string length
    assert common_utils.truncate_long_string(
        'abcde', 4, truncate_middle=True) == 'a...' 
