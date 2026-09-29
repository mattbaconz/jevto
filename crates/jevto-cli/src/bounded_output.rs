use std::io::{self, Read, Write};
use std::process::{Command, ExitStatus, Output, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;

const DEFAULT_CAPTURE_LIMIT: usize = 64 * 1024 * 1024;

pub enum ChildOutput {
    Captured(Output),
    Bypassed { status: ExitStatus, raw_bytes: u64 },
}

#[derive(Clone, Copy)]
pub enum OverflowBehavior {
    StreamOriginal,
    Discard,
}

#[derive(Clone, Copy)]
enum ChildStream {
    Stdout,
    Stderr,
}

struct BufferedStreams {
    stdout: Vec<u8>,
    stderr: Vec<u8>,
    limit: usize,
    raw_bytes: u64,
    bypassed: bool,
    overflow_behavior: OverflowBehavior,
}

impl BufferedStreams {
    fn accept(&mut self, stream: ChildStream, bytes: &[u8]) -> io::Result<()> {
        self.raw_bytes = self.raw_bytes.saturating_add(bytes.len() as u64);
        if !self.bypassed
            && self
                .stdout
                .len()
                .saturating_add(self.stderr.len())
                .saturating_add(bytes.len())
                > self.limit
        {
            self.bypassed = true;
            if matches!(self.overflow_behavior, OverflowBehavior::StreamOriginal) {
                forward(ChildStream::Stdout, &self.stdout)?;
                forward(ChildStream::Stderr, &self.stderr)?;
            }
            self.stdout.clear();
            self.stderr.clear();
        }
        if self.bypassed {
            match self.overflow_behavior {
                OverflowBehavior::StreamOriginal => forward(stream, bytes),
                OverflowBehavior::Discard => Ok(()),
            }
        } else {
            match stream {
                ChildStream::Stdout => self.stdout.extend_from_slice(bytes),
                ChildStream::Stderr => self.stderr.extend_from_slice(bytes),
            }
            Ok(())
        }
    }
}

/// Pass bypassed bytes straight through. Rust's stdout is line-buffered, so
/// a partial line would sit in the buffer (and be lost if the process exits
/// through `std::process::exit`) without an explicit flush.
fn forward(stream: ChildStream, bytes: &[u8]) -> io::Result<()> {
    match stream {
        ChildStream::Stdout => {
            let mut out = io::stdout().lock();
            out.write_all(bytes)?;
            out.flush()
        }
        ChildStream::Stderr => io::stderr().write_all(bytes),
    }
}

fn drain<R: Read + Send + 'static>(
    mut source: R,
    shared: Arc<Mutex<BufferedStreams>>,
    stream: ChildStream,
) -> thread::JoinHandle<io::Result<()>> {
    thread::spawn(move || {
        let mut chunk = [0u8; 16 * 1024];
        let mut first_error = None;
        loop {
            let count = source.read(&mut chunk)?;
            if count == 0 {
                break;
            }
            let accepted = shared
                .lock()
                .map_err(|_| io::Error::other("child output state poisoned"))?
                .accept(stream, &chunk[..count]);
            if let Err(error) = accepted {
                // Keep draining both pipes so a closed parent output cannot strand the child.
                first_error.get_or_insert(error);
            }
        }
        first_error.map_or(Ok(()), Err)
    })
}

/// Windows `CreateProcess` only finds `.exe` files on PATH, but npm-installed
/// tools (`tsc`, `eslint`, `npx`, `npm` itself) are `.cmd` shims. Resolve a
/// bare program name through PATH and PATHEXT the way a shell would; Rust's
/// `Command` runs a resolved `.cmd`/`.bat` through `cmd.exe` with safe argument
/// quoting. Elsewhere, and for names with a path or extension, this is a no-op.
pub fn resolve_program(executable: &str) -> std::path::PathBuf {
    let bare = std::path::Path::new(executable);
    if !cfg!(windows) || bare.components().count() != 1 || bare.extension().is_some() {
        return bare.into();
    }
    let extensions = std::env::var("PATHEXT").unwrap_or_else(|_| ".COM;.EXE;.BAT;.CMD".into());
    let path = std::env::var_os("PATH").unwrap_or_default();
    for directory in std::env::split_paths(&path) {
        for extension in extensions.split(';').filter(|extension| {
            [".com", ".exe", ".bat", ".cmd"].contains(&extension.to_ascii_lowercase().as_str())
        }) {
            let candidate = directory.join(format!("{executable}{extension}"));
            if candidate.is_file() {
                return candidate;
            }
        }
    }
    bare.into()
}

pub fn capture_limit(store_max_bytes: u64) -> usize {
    let configured = std::env::var("JEVTO_MAX_CAPTURE_BYTES")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(DEFAULT_CAPTURE_LIMIT);
    let quota_remaining_for_output = store_max_bytes.saturating_sub(4096);
    configured.min(usize::try_from(quota_remaining_for_output).unwrap_or(usize::MAX))
}

pub fn run(executable: &str, arguments: &[String], limit: usize) -> io::Result<ChildOutput> {
    let mut command = Command::new(resolve_program(executable));
    command.args(arguments);
    run_command(command, limit, OverflowBehavior::StreamOriginal)
}

pub fn run_command(
    mut command: Command,
    limit: usize,
    overflow_behavior: OverflowBehavior,
) -> io::Result<ChildOutput> {
    let mut child = command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let shared = Arc::new(Mutex::new(BufferedStreams {
        stdout: Vec::new(),
        stderr: Vec::new(),
        limit,
        raw_bytes: 0,
        bypassed: false,
        overflow_behavior,
    }));
    let stdout = drain(
        child.stdout.take().expect("piped stdout"),
        Arc::clone(&shared),
        ChildStream::Stdout,
    );
    let stderr = drain(
        child.stderr.take().expect("piped stderr"),
        Arc::clone(&shared),
        ChildStream::Stderr,
    );
    let status = child.wait()?;
    let stdout_result = stdout.join();
    let stderr_result = stderr.join();
    let stdout_result =
        stdout_result.map_err(|_| io::Error::other("child stdout reader panicked"))?;
    let stderr_result =
        stderr_result.map_err(|_| io::Error::other("child stderr reader panicked"))?;
    stdout_result?;
    stderr_result?;
    let state = Arc::try_unwrap(shared)
        .map_err(|_| io::Error::other("child output readers remain active"))?
        .into_inner()
        .map_err(|_| io::Error::other("child output state poisoned"))?;
    if state.bypassed {
        Ok(ChildOutput::Bypassed {
            status,
            raw_bytes: state.raw_bytes,
        })
    } else {
        Ok(ChildOutput::Captured(Output {
            status,
            stdout: state.stdout,
            stderr: state.stderr,
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::resolve_program;

    #[test]
    fn resolves_bare_windows_programs_through_pathext_only() {
        assert_eq!(
            resolve_program("jevto-no-such-program"),
            std::path::PathBuf::from("jevto-no-such-program")
        );
        assert_eq!(
            resolve_program("dir/tool"),
            std::path::PathBuf::from("dir/tool")
        );
        if cfg!(windows) {
            let cmd = resolve_program("cmd");
            let name = cmd.to_string_lossy().to_ascii_lowercase();
            assert!(cmd.is_absolute() && name.ends_with("cmd.exe"), "{cmd:?}");
        }
    }
}
