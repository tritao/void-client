use std::env;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::thread;

#[derive(Clone)]
struct RewritePattern {
    source_class: String,
    member_name: String,
    replacement: String,
}

#[derive(Copy, Clone, Eq, PartialEq)]
enum Mode {
    Code,
    LineComment,
    BlockComment,
    String,
    Char,
}

struct CliArgs {
    src_dir: PathBuf,
    patterns_file: PathBuf,
    changed_out: Option<PathBuf>,
    jobs: usize,
    dry_run: bool,
}

fn parse_args() -> Result<CliArgs, String> {
    let mut src_dir: Option<PathBuf> = None;
    let mut patterns_file: Option<PathBuf> = None;
    let mut changed_out: Option<PathBuf> = None;
    let mut jobs: usize = 1;
    let mut dry_run = false;

    let args: Vec<String> = env::args().collect();
    let mut i = 1usize;
    while i < args.len() {
        match args[i].as_str() {
            "--src-dir" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --src-dir".to_string());
                }
                src_dir = Some(PathBuf::from(&args[i]));
            }
            "--patterns-file" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --patterns-file".to_string());
                }
                patterns_file = Some(PathBuf::from(&args[i]));
            }
            "--changed-out" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --changed-out".to_string());
                }
                changed_out = Some(PathBuf::from(&args[i]));
            }
            "--jobs" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --jobs".to_string());
                }
                jobs = args[i]
                    .parse::<usize>()
                    .map_err(|_| format!("invalid --jobs value: {}", args[i]))?;
                if jobs == 0 {
                    jobs = 1;
                }
            }
            "--dry-run" => dry_run = true,
            "--help" | "-h" => {
                return Err(
                    "usage: callsite_rewriter --src-dir <dir> --patterns-file <file> [--jobs <n>] [--dry-run] [--changed-out <file>]"
                        .to_string(),
                );
            }
            unknown => return Err(format!("unknown argument: {}", unknown)),
        }
        i += 1;
    }

    let src_dir = src_dir.ok_or_else(|| "missing required --src-dir".to_string())?;
    let patterns_file = patterns_file.ok_or_else(|| "missing required --patterns-file".to_string())?;
    Ok(CliArgs {
        src_dir,
        patterns_file,
        changed_out,
        jobs,
        dry_run,
    })
}

fn is_java_ident_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'$'
}

fn is_boundary_before(bytes: &[u8], idx: usize) -> bool {
    if idx == 0 {
        return true;
    }
    !is_java_ident_char(bytes[idx - 1])
}

fn is_boundary_after(bytes: &[u8], idx: usize) -> bool {
    if idx >= bytes.len() {
        return true;
    }
    !is_java_ident_char(bytes[idx])
}

fn skip_ascii_ws(bytes: &[u8], mut idx: usize) -> usize {
    while idx < bytes.len() && bytes[idx].is_ascii_whitespace() {
        idx += 1;
    }
    idx
}

fn replace_pattern_in_segment(segment: &str, pattern: &RewritePattern) -> String {
    let bytes = segment.as_bytes();
    let source = pattern.source_class.as_bytes();
    let member = pattern.member_name.as_bytes();
    if source.is_empty() || member.is_empty() || bytes.len() < source.len() + 1 + member.len() {
        return segment.to_string();
    }

    let mut out = String::with_capacity(segment.len());
    let mut i = 0usize;
    let mut last = 0usize;

    while i + source.len() <= bytes.len() {
        if bytes[i..(i + source.len())] != *source || !is_boundary_before(bytes, i) || !is_boundary_after(bytes, i + source.len()) {
            i += 1;
            continue;
        }

        let mut j = skip_ascii_ws(bytes, i + source.len());
        if j >= bytes.len() || bytes[j] != b'.' {
            i += 1;
            continue;
        }
        j += 1;
        j = skip_ascii_ws(bytes, j);
        if j + member.len() > bytes.len() || bytes[j..(j + member.len())] != *member || !is_boundary_after(bytes, j + member.len()) {
            i += 1;
            continue;
        }

        out.push_str(&segment[last..i]);
        out.push_str(&pattern.replacement);
        i = j + member.len();
        last = i;
    }

    if last == 0 {
        return segment.to_string();
    }
    out.push_str(&segment[last..]);
    out
}

fn apply_patterns(segment: &str, patterns: &[RewritePattern]) -> String {
    let mut out = segment.to_string();
    for pattern in patterns {
        out = replace_pattern_in_segment(&out, pattern);
    }
    out
}

fn rewrite_non_comment_string(text: &str, patterns: &[RewritePattern]) -> String {
    let bytes = text.as_bytes();
    let n = bytes.len();
    let mut i = 0usize;
    let mut segment_start = 0usize;
    let mut mode = Mode::Code;
    let mut out = String::with_capacity(text.len());

    while i < n {
        let ch = bytes[i];
        let next = if i + 1 < n { bytes[i + 1] } else { 0 };
        match mode {
            Mode::Code => {
                if ch == b'/' && next == b'/' {
                    out.push_str(&apply_patterns(&text[segment_start..i], patterns));
                    segment_start = i;
                    mode = Mode::LineComment;
                    i += 2;
                    continue;
                }
                if ch == b'/' && next == b'*' {
                    out.push_str(&apply_patterns(&text[segment_start..i], patterns));
                    segment_start = i;
                    mode = Mode::BlockComment;
                    i += 2;
                    continue;
                }
                if ch == b'"' {
                    out.push_str(&apply_patterns(&text[segment_start..i], patterns));
                    segment_start = i;
                    mode = Mode::String;
                    i += 1;
                    continue;
                }
                if ch == b'\'' {
                    out.push_str(&apply_patterns(&text[segment_start..i], patterns));
                    segment_start = i;
                    mode = Mode::Char;
                    i += 1;
                    continue;
                }
                i += 1;
            }
            Mode::LineComment => {
                if ch == b'\n' {
                    out.push_str(&text[segment_start..(i + 1)]);
                    segment_start = i + 1;
                    mode = Mode::Code;
                }
                i += 1;
            }
            Mode::BlockComment => {
                if ch == b'*' && next == b'/' {
                    i += 2;
                    out.push_str(&text[segment_start..i]);
                    segment_start = i;
                    mode = Mode::Code;
                    continue;
                }
                i += 1;
            }
            Mode::String => {
                if ch == b'\\' && i + 1 < n {
                    i += 2;
                    continue;
                }
                if ch == b'"' {
                    i += 1;
                    out.push_str(&text[segment_start..i]);
                    segment_start = i;
                    mode = Mode::Code;
                    continue;
                }
                i += 1;
            }
            Mode::Char => {
                if ch == b'\\' && i + 1 < n {
                    i += 2;
                    continue;
                }
                if ch == b'\'' {
                    i += 1;
                    out.push_str(&text[segment_start..i]);
                    segment_start = i;
                    mode = Mode::Code;
                    continue;
                }
                i += 1;
            }
        }
    }

    let tail = &text[segment_start..];
    if mode == Mode::Code {
        out.push_str(&apply_patterns(tail, patterns));
    } else {
        out.push_str(tail);
    }
    out
}

fn collect_java_files(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), Box<dyn Error>> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        if entry.file_type()?.is_dir() {
            collect_java_files(&path, out)?;
            continue;
        }
        if path.extension().and_then(|x| x.to_str()) == Some("java") {
            out.push(path);
        }
    }
    Ok(())
}

fn load_patterns(path: &Path) -> Result<Vec<RewritePattern>, Box<dyn Error>> {
    let mut out = Vec::new();
    let raw = fs::read_to_string(path)?;
    for line in raw.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        let cols: Vec<&str> = line.split('\t').collect();
        if cols.len() != 4 {
            return Err(format!("invalid row (expected 4 tab-separated columns): {}", line).into());
        }
        let source_class = cols[0].trim().to_string();
        let member_name = cols[1].trim().to_string();
        let target_class = cols[2].trim().to_string();
        let target_name = cols[3].trim().to_string();
        if source_class.is_empty() || member_name.is_empty() || target_class.is_empty() || target_name.is_empty() {
            return Err(format!("empty field in row: {}", line).into());
        }
        out.push(RewritePattern {
            source_class,
            member_name,
            replacement: format!("{}.{}", target_class, target_name),
        });
    }
    Ok(out)
}

fn rewrite_file(path: &Path, patterns: &[RewritePattern], dry_run: bool) -> Result<Option<String>, Box<dyn Error>> {
    let bytes = fs::read(path)?;
    let text = String::from_utf8_lossy(&bytes);
    let rewritten = rewrite_non_comment_string(text.as_ref(), patterns);
    if rewritten == text {
        return Ok(None);
    }
    if !dry_run {
        fs::write(path, rewritten.as_bytes())?;
    }
    let resolved = fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    Ok(Some(resolved.to_string_lossy().to_string()))
}

fn process_chunk(files: Vec<PathBuf>, patterns: Arc<Vec<RewritePattern>>, dry_run: bool) -> Vec<String> {
    let mut changed: Vec<String> = Vec::new();
    for path in files {
        match rewrite_file(&path, &patterns, dry_run) {
            Ok(Some(file)) => changed.push(file),
            Ok(None) => {}
            Err(err) => eprintln!("error: {}: {}", path.display(), err),
        }
    }
    changed
}

fn run(args: CliArgs) -> Result<(), Box<dyn Error>> {
    let patterns = load_patterns(&args.patterns_file)?;
    if patterns.is_empty() {
        if let Some(changed_out) = args.changed_out {
            if let Some(parent) = changed_out.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(changed_out, "")?;
        }
        return Ok(());
    }

    let mut java_files = Vec::new();
    collect_java_files(&args.src_dir, &mut java_files)?;
    java_files.sort();

    let mut jobs = args.jobs.max(1);
    if jobs > java_files.len() && !java_files.is_empty() {
        jobs = java_files.len();
    }
    let patterns = Arc::new(patterns);
    let mut changed: Vec<String> = Vec::new();

    if jobs <= 1 || java_files.is_empty() {
        changed = process_chunk(java_files, patterns, args.dry_run);
    } else {
        let chunk_size = java_files.len().div_ceil(jobs);
        let mut handles = Vec::new();
        for chunk in java_files.chunks(chunk_size) {
            let files = chunk.to_vec();
            let patterns_clone = Arc::clone(&patterns);
            let dry_run = args.dry_run;
            handles.push(thread::spawn(move || process_chunk(files, patterns_clone, dry_run)));
        }
        for handle in handles {
            match handle.join() {
                Ok(mut part) => changed.append(&mut part),
                Err(_) => return Err("worker thread panicked".into()),
            }
        }
    }

    changed.sort();
    if let Some(changed_out) = args.changed_out {
        if let Some(parent) = changed_out.parent() {
            fs::create_dir_all(parent)?;
        }
        let mut payload = String::new();
        for item in &changed {
            payload.push_str(item);
            payload.push('\n');
        }
        fs::write(changed_out, payload)?;
    } else {
        for item in &changed {
            println!("{}", item);
        }
    }
    eprintln!("changed_files={}", changed.len());
    Ok(())
}

fn main() {
    match parse_args() {
        Ok(args) => {
            if let Err(err) = run(args) {
                eprintln!("{}", err);
                std::process::exit(1);
            }
        }
        Err(err) => {
            eprintln!("{}", err);
            std::process::exit(2);
        }
    }
}
