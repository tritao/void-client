use std::collections::HashSet;
use std::env;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::thread;

struct CliArgs {
    src_dir: PathBuf,
    tokens_file: PathBuf,
    out_file: PathBuf,
    files_list: Option<PathBuf>,
    jobs: usize,
}

fn parse_args() -> Result<CliArgs, String> {
    let mut src_dir: Option<PathBuf> = None;
    let mut tokens_file: Option<PathBuf> = None;
    let mut out_file: Option<PathBuf> = None;
    let mut files_list: Option<PathBuf> = None;
    let mut jobs: usize = 1;

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
            "--tokens-file" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --tokens-file".to_string());
                }
                tokens_file = Some(PathBuf::from(&args[i]));
            }
            "--out-file" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --out-file".to_string());
                }
                out_file = Some(PathBuf::from(&args[i]));
            }
            "--files-list" => {
                i += 1;
                if i >= args.len() {
                    return Err("missing value for --files-list".to_string());
                }
                files_list = Some(PathBuf::from(&args[i]));
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
            "--help" | "-h" => {
                return Err(
                    "usage: rename_token_prefilter --src-dir <dir> --tokens-file <file> --out-file <file> [--files-list <file>] [--jobs <n>]"
                        .to_string(),
                );
            }
            unknown => return Err(format!("unknown argument: {}", unknown)),
        }
        i += 1;
    }

    Ok(CliArgs {
        src_dir: src_dir.ok_or_else(|| "missing required --src-dir".to_string())?,
        tokens_file: tokens_file.ok_or_else(|| "missing required --tokens-file".to_string())?,
        out_file: out_file.ok_or_else(|| "missing required --out-file".to_string())?,
        files_list,
        jobs,
    })
}

fn is_ident_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'$'
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

fn load_files_from_list(path: &Path) -> Result<Vec<PathBuf>, Box<dyn Error>> {
    let mut out = Vec::new();
    let raw = fs::read_to_string(path)?;
    for line in raw.lines() {
        let value = line.trim();
        if value.is_empty() || value.starts_with('#') {
            continue;
        }
        out.push(PathBuf::from(value));
    }
    Ok(out)
}

fn load_tokens(path: &Path) -> Result<HashSet<String>, Box<dyn Error>> {
    let mut out = HashSet::new();
    let raw = fs::read_to_string(path)?;
    for line in raw.lines() {
        let token = line.trim();
        if token.is_empty() || token.starts_with('#') {
            continue;
        }
        out.insert(token.to_string());
    }
    Ok(out)
}

fn file_has_any_token(path: &Path, tokens: &HashSet<String>) -> Result<bool, Box<dyn Error>> {
    let data = fs::read(path)?;
    let mut i = 0usize;
    let n = data.len();
    while i < n {
        if !is_ident_char(data[i]) {
            i += 1;
            continue;
        }
        if i > 0 && is_ident_char(data[i - 1]) {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        while j < n && is_ident_char(data[j]) {
            j += 1;
        }
        if let Ok(tok) = std::str::from_utf8(&data[i..j]) {
            if tokens.contains(tok) {
                return Ok(true);
            }
        }
        i = j;
    }
    Ok(false)
}

fn process_chunk(files: Vec<PathBuf>, tokens: Arc<HashSet<String>>) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    for path in files {
        match file_has_any_token(&path, &tokens) {
            Ok(true) => out.push(path),
            Ok(false) => {}
            Err(err) => eprintln!("error: {}: {}", path.display(), err),
        }
    }
    out
}

fn run(args: CliArgs) -> Result<(), Box<dyn Error>> {
    let tokens = load_tokens(&args.tokens_file)?;
    if tokens.is_empty() {
        if let Some(parent) = args.out_file.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(&args.out_file, "")?;
        println!("matched_files=0");
        return Ok(());
    }

    let mut files = if let Some(list_path) = args.files_list.as_ref() {
        load_files_from_list(list_path)?
    } else {
        let mut scanned: Vec<PathBuf> = Vec::new();
        collect_java_files(&args.src_dir, &mut scanned)?;
        scanned
    };
    files.sort();

    let mut jobs = args.jobs.max(1);
    if jobs > files.len() && !files.is_empty() {
        jobs = files.len();
    }

    let tokens = Arc::new(tokens);
    let mut matched: Vec<PathBuf> = Vec::new();
    if jobs <= 1 || files.is_empty() {
        matched = process_chunk(files, tokens);
    } else {
        let chunk_size = files.len().div_ceil(jobs);
        let mut handles = Vec::new();
        for chunk in files.chunks(chunk_size) {
            let chunk_vec = chunk.to_vec();
            let tokens_clone = Arc::clone(&tokens);
            handles.push(thread::spawn(move || process_chunk(chunk_vec, tokens_clone)));
        }
        for handle in handles {
            match handle.join() {
                Ok(mut part) => matched.append(&mut part),
                Err(_) => return Err("worker thread panicked".into()),
            }
        }
    }
    matched.sort();
    if let Some(parent) = args.out_file.parent() {
        fs::create_dir_all(parent)?;
    }
    let mut payload = String::new();
    for path in &matched {
        payload.push_str(&path.to_string_lossy());
        payload.push('\n');
    }
    fs::write(&args.out_file, payload)?;
    println!("matched_files={}", matched.len());
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
