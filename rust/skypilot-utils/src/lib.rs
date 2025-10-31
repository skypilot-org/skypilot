//! Collection of utility functions kicking off the Rust port of
//! `sky.utils.common_utils`.

use std::cmp::min;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::path::Path;

const DEFAULT_CHUNK_SIZE: usize = 8 * 1024;

/// Reads the last `n` lines of a file and returns them as a vector of strings.
///
/// Mirrors the behaviour of
/// `sky.utils.common_utils.read_last_n_lines`:
/// * All lines except the last keep a trailing `\n`.
/// * Decoding errors are replaced using the Unicode replacement character
///   (`U+FFFD`).
pub fn read_last_n_lines<P: AsRef<Path>>(path: P, n: usize) -> io::Result<Vec<String>> {
    if n == 0 {
        return Ok(Vec::new());
    }

    let path_ref = path.as_ref();
    let mut file = File::open(path_ref)?;
    let file_size = file.metadata()?.len();
    if file_size == 0 {
        return Ok(Vec::new());
    }

    let mut position = file_size;
    let mut lines_found = 0usize;
    let mut chunks: Vec<Vec<u8>> = Vec::new();

    while position > 0 && lines_found <= n {
        let read_size = min(DEFAULT_CHUNK_SIZE as u64, position) as usize;
        position -= read_size as u64;
        file.seek(SeekFrom::Start(position))?;

        let mut buffer = vec![0u8; read_size];
        file.read_exact(&mut buffer)?;
        lines_found += bytecount::count(&buffer, b'\n');
        chunks.push(buffer);
    }

    let total_len: usize = chunks.iter().map(|chunk| chunk.len()).sum();
    let mut full_bytes = Vec::with_capacity(total_len);
    for chunk in chunks.iter().rev() {
        full_bytes.extend_from_slice(chunk);
    }

    // Split at line feeds; carriage returns will be stripped later.
    let all_lines: Vec<Vec<u8>> = full_bytes
        .split(|&b| b == b'\n')
        .map(|slice| slice.to_vec())
        .collect();

    if all_lines.is_empty() {
        return Ok(Vec::new());
    }

    let ends_with_newline = all_lines.last().map_or(false, |last| last.is_empty());

    let result_bytes: Vec<Vec<u8>> = if ends_with_newline {
        let len = all_lines.len();
        let start = len.saturating_sub(n + 1);
        all_lines[start..len - 1].to_vec()
    } else {
        let len = all_lines.len();
        let start = len.saturating_sub(n);
        all_lines[start..len].to_vec()
    };

    if result_bytes.is_empty() {
        return Ok(Vec::new());
    }

    let mut decoded_lines = Vec::with_capacity(result_bytes.len());
    for line_bytes in result_bytes.iter().take(result_bytes.len().saturating_sub(1)) {
        let mut line = String::from_utf8_lossy(line_bytes).into_owned();
        if line.ends_with('\r') {
            line.pop();
        }
        line.push('\n');
        decoded_lines.push(line);
    }

    if let Some(last_bytes) = result_bytes.last() {
        let mut last_line = String::from_utf8_lossy(last_bytes).into_owned();
        if last_line.ends_with('\r') {
            last_line.pop();
        }
        decoded_lines.push(last_line);
    }

    Ok(decoded_lines)
}

#[cfg(test)]
mod tests {
    use super::read_last_n_lines;
    use std::fs::{self, File};
    use std::io::Write;
    use tempfile::tempdir;

    #[test]
    fn returns_empty_for_zero_lines() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("sample.log");
        File::create(&file_path).unwrap();

        let result = read_last_n_lines(&file_path, 0).unwrap();
        assert!(result.is_empty());
    }

    #[test]
    fn reads_last_lines_with_newline_termination() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("log.txt");

        let mut file = File::create(&file_path).unwrap();
        writeln!(file, "line1").unwrap();
        writeln!(file, "line2").unwrap();
        writeln!(file, "line3").unwrap();

        let result = read_last_n_lines(&file_path, 2).unwrap();
        assert_eq!(result, vec!["line2\n".to_string(), "line3".to_string()]);
    }

    #[test]
    fn reads_when_file_has_no_trailing_newline() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("partial.log");

        let mut file = File::create(&file_path).unwrap();
        writeln!(file, "line1").unwrap();
        write!(file, "line2").unwrap();

        let result = read_last_n_lines(&file_path, 2).unwrap();
        assert_eq!(result, vec!["line1\n".to_string(), "line2".to_string()]);
    }

    #[test]
    fn handles_more_lines_requested_than_exist() {
        let dir = tempdir().unwrap();
        let file_path = dir.path().join("short.log");

        fs::write(&file_path, b"only one line\n").unwrap();
        let result = read_last_n_lines(&file_path, 5).unwrap();

        assert_eq!(result, vec!["only one line".to_string()]);
    }
}
