//! String utility functions
//!
//! High-performance string manipulation operations:
//! - Base36 encoding
//! - Float formatting
//! - String truncation

use pyo3::prelude::*;

use crate::errors::SkyPilotError;

/// Encode a hexadecimal string to base36.
///
/// # Arguments
/// * `hex_str` - Hexadecimal string to encode
///
/// # Returns
/// Base36 encoded string
#[pyfunction]
#[pyo3(signature = (hex_str))]
pub fn base36_encode(hex_str: &str) -> PyResult<String> {
    // Remove '0x' prefix if present
    let hex_str = hex_str.trim_start_matches("0x");

    // Parse hex string to number
    let num = u128::from_str_radix(hex_str, 16)
        .map_err(|e| SkyPilotError::ParseError(format!("Invalid hex string: {}", e)))?;

    // Convert to base36
    const BASE36_CHARS: &[u8] = b"0123456789abcdefghijklmnopqrstuvwxyz";

    if num == 0 {
        return Ok("0".to_string());
    }

    let mut result = Vec::new();
    let mut n = num;

    while n > 0 {
        result.push(BASE36_CHARS[(n % 36) as usize]);
        n /= 36;
    }

    result.reverse();
    Ok(String::from_utf8(result).unwrap())
}

/// Format a float with specified precision.
///
/// # Arguments
/// * `num` - Number to format
/// * `precision` - Number of decimal places
///
/// # Returns
/// Formatted string representation
#[pyfunction]
#[pyo3(signature = (num, precision = 1))]
pub fn format_float(num: f64, precision: usize) -> PyResult<String> {
    if num.is_nan() {
        return Ok("NaN".to_string());
    }
    if num.is_infinite() {
        return Ok(if num > 0.0 { "?" } else { "-?" }.to_string());
    }

    // Use intelligent formatting
    if num.abs() >= 1000.0 {
        // Use K, M, B, T suffixes for large numbers
        let (value, suffix) = if num.abs() >= 1e12 {
            (num / 1e12, "T")
        } else if num.abs() >= 1e9 {
            (num / 1e9, "B")
        } else if num.abs() >= 1e6 {
            (num / 1e6, "M")
        } else if num.abs() >= 1e3 {
            (num / 1e3, "K")
        } else {
            (num, "")
        };
        Ok(format!("{:.prec$}{}", value, suffix, prec = precision))
    } else {
        Ok(format!("{:.prec$}", num, prec = precision))
    }
}

/// Truncate a string to a maximum length, adding ellipsis if needed.
///
/// # Arguments
/// * `s` - String to truncate
/// * `max_length` - Maximum length (default: 80)
/// * `placeholder` - Placeholder for truncated part (default: "...")
///
/// # Returns
/// Truncated string
#[pyfunction]
#[pyo3(signature = (s, max_length = 80, placeholder = "..."))]
pub fn truncate_long_string(s: &str, max_length: usize, placeholder: &str) -> PyResult<String> {
    if s.len() <= max_length {
        return Ok(s.to_string());
    }

    let placeholder_len = placeholder.len();
    if max_length <= placeholder_len {
        return Ok(placeholder[..max_length].to_string());
    }

    let keep_len = max_length - placeholder_len;
    Ok(format!("{}{}", &s[..keep_len], placeholder))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_base36_encode() {
        assert_eq!(base36_encode("ff").unwrap(), "4z");
        assert_eq!(base36_encode("0x10").unwrap(), "g");
        assert_eq!(base36_encode("100").unwrap(), "74");
    }

    #[test]
    fn test_format_float() {
        assert_eq!(format_float(1234.5, 1).unwrap(), "1.2K");
        assert_eq!(format_float(1234567.89, 2).unwrap(), "1.23M");
        assert_eq!(format_float(0.123, 2).unwrap(), "0.12");
    }

    #[test]
    fn test_truncate_long_string() {
        let long = "This is a very long string that needs truncation";
        let result = truncate_long_string(long, 20, "...").unwrap();
        assert_eq!(result.len(), 20);
        assert!(result.ends_with("..."));
    }
}
