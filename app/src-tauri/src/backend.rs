use std::io::Write;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use base64::Engine;
use tauri::{AppHandle, Manager};

use crate::paths::{get_elato_dir, get_images_dir, get_venv_python, get_voices_dir};

pub struct ApiProcess(pub Mutex<Option<Child>>);

fn resolve_python_backend_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let backend_dir = if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo_root = manifest_dir
            .parent()
            .and_then(|p| p.parent())
            .ok_or_else(|| "Failed to resolve repo root from CARGO_MANIFEST_DIR".to_string())?;
        repo_root.join("resources").join("python-backend")
    } else {
        app.path()
            .resource_dir()
            .map_err(|e| format!("Failed to resolve app resource dir: {e}"))?
            .join("python-backend")
    };

    if !backend_dir.join("server.py").exists() {
        return Err(format!(
            "python-backend resources not found at deterministic path: {}",
            backend_dir.display()
        ));
    }

    Ok(backend_dir)
}

fn resolve_firmware_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let firmware_dir = if cfg!(debug_assertions) {
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let repo_root = manifest_dir
            .parent()
            .and_then(|p| p.parent())
            .ok_or_else(|| "Failed to resolve repo root from CARGO_MANIFEST_DIR".to_string())?;
        repo_root.join("resources").join("firmware")
    } else {
        app.path()
            .resource_dir()
            .map_err(|e| format!("Failed to resolve app resource dir: {e}"))?
            .join("firmware")
    };

    Ok(firmware_dir)
}

fn resolve_arduino_dir() -> Option<PathBuf> {
    if !cfg!(debug_assertions) {
        return None;
    }
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let repo_root = manifest_dir.parent().and_then(|p| p.parent())?;
    Some(repo_root.join("arduino"))
}

pub fn ensure_port_free(port: u16) {
    let addr = ("127.0.0.1", port);

    if TcpStream::connect(addr).is_ok() {
        if port == 8000 {
            let _ = TcpStream::connect(addr).and_then(|mut stream| {
                let req =
                    b"POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n";
                stream.write_all(req)
            });
            std::thread::sleep(Duration::from_millis(500));
        }

        if cfg!(unix) {
            let _ = Command::new("sh")
                .arg("-c")
                .arg(format!("lsof -ti:{} | xargs kill -9", port))
                .output();
        }

        for _ in 0..30 {
            std::thread::sleep(Duration::from_millis(100));
            if TcpStream::connect(addr).is_err() {
                break;
            }
        }
    }
}

pub fn stop_api_server(app: &tauri::AppHandle) {
    let _ = TcpStream::connect(("127.0.0.1", 8000)).and_then(|mut stream| {
        let req = b"POST /shutdown HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\n\r\n";
        stream.write_all(req)
    });

    std::thread::sleep(Duration::from_millis(200));

    if let Some(state) = app.try_state::<ApiProcess>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
            }
        }
    }

    if cfg!(unix) {
        let _ = Command::new("sh")
            .arg("-c")
            .arg("lsof -ti:8000 | xargs kill -9")
            .output();
    }
}

#[tauri::command]
pub async fn start_backend(app: AppHandle) -> Result<String, String> {
    if TcpStream::connect_timeout(
        &"127.0.0.1:8000".parse().unwrap(),
        Duration::from_millis(100),
    )
    .is_ok()
    {
        return Ok("Backend already running".to_string());
    }

    let venv_python = get_venv_python(&app);
    if !venv_python.exists() {
        return Err("Python environment not ready".to_string());
    }

    let python_dir = resolve_python_backend_dir(&app)?;

    let elato_db_path = get_elato_dir(&app).join("elato.db");
    let elato_voices_dir = get_voices_dir(&app);
    let elato_images_dir = get_images_dir(&app);
    let firmware_dir = resolve_firmware_dir(&app)?;

    ensure_port_free(8000);

    let mut cmd = Command::new(&venv_python);
    cmd.arg("-m")
        .arg("uvicorn")
        .arg("server:app")
        .arg("--host")
        .arg("0.0.0.0")
        .arg("--port")
        .arg("8000")
        .current_dir(&python_dir)
        .env("ELATO_DB_PATH", elato_db_path.to_string_lossy().to_string())
        .env(
            "ELATO_VOICES_DIR",
            elato_voices_dir.to_string_lossy().to_string(),
        )
        .env(
            "ELATO_IMAGES_DIR",
            elato_images_dir.to_string_lossy().to_string(),
        )
        .env("ELATO_FIRMWARE_DIR", firmware_dir.to_string_lossy().to_string())
        .env("TOKENIZERS_PARALLELISM", "false")
        .env("HF_HUB_DISABLE_XET", "1")
        .env("HF_HUB_ENABLE_HF_TRANSFER", "1")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    if let Some(arduino_dir) = resolve_arduino_dir() {
        cmd.env("ELATO_ARDUINO_DIR", arduino_dir.to_string_lossy().to_string());
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("Failed to start backend: {e}"))?;

    println!("[TAURI] Backend started after setup (PID: {})", child.id());
    app.manage(ApiProcess(Mutex::new(Some(child))));

    Ok("Backend started".to_string())
}

pub fn setup_backend(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    ensure_port_free(8000);

    let app_handle = app.handle();
    let venv_python = get_venv_python(&app_handle);

    let python_dir = match resolve_python_backend_dir(&app_handle) {
        Ok(path) => path,
        Err(e) => {
            eprintln!("[TAURI] {}", e);
            return Ok(());
        }
    };

    if !venv_python.exists() {
        println!("[TAURI] Python env not ready yet. Skipping API server start.");
        return Ok(());
    }

    let python_path = venv_python;

    println!("[TAURI] Starting Python API server...");
    println!("[TAURI] Python: {:?}", python_path);
    println!("[TAURI] Server dir: {:?}", python_dir);

    let elato_db_path = get_elato_dir(&app_handle).join("elato.db");
    let elato_voices_dir = get_voices_dir(&app_handle);
    let elato_images_dir = get_images_dir(&app_handle);
    println!("[TAURI] DB Path: {:?}", elato_db_path);
    let firmware_dir = resolve_firmware_dir(&app_handle)?;

    let mut cmd = Command::new(&python_path);
    cmd.arg("-m")
        .arg("uvicorn")
        .arg("server:app")
        .arg("--host")
        .arg("0.0.0.0")
        .arg("--port")
        .arg("8000")
        .current_dir(&python_dir)
        .env("ELATO_DB_PATH", elato_db_path.to_string_lossy().to_string())
        .env(
            "ELATO_VOICES_DIR",
            elato_voices_dir.to_string_lossy().to_string(),
        )
        .env(
            "ELATO_IMAGES_DIR",
            elato_images_dir.to_string_lossy().to_string(),
        )
        .env("ELATO_FIRMWARE_DIR", firmware_dir.to_string_lossy().to_string())
        .env("TOKENIZERS_PARALLELISM", "false")
        .env("HF_HUB_DISABLE_XET", "1")
        .env("HF_HUB_ENABLE_HF_TRANSFER", "1")
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    if let Some(arduino_dir) = resolve_arduino_dir() {
        cmd.env("ELATO_ARDUINO_DIR", arduino_dir.to_string_lossy().to_string());
    }

    let child = cmd.spawn();

    match child {
        Ok(child) => {
            println!("[TAURI] Python API server started (PID: {})", child.id());
            app.manage(ApiProcess(Mutex::new(Some(child))));
        }
        Err(e) => {
            eprintln!("[TAURI] Failed to start Python API server: {}", e);
        }
    }

    Ok(())
}

#[tauri::command]
pub fn save_voice_wav_base64(
    app: AppHandle,
    voice_id: String,
    base64_wav: String,
) -> Result<String, String> {
    let voice_id = voice_id.trim();
    if voice_id.is_empty() {
        return Err("voice_id is required".to_string());
    }
    if !voice_id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return Err("voice_id contains invalid characters".to_string());
    }

    let bytes = base64::engine::general_purpose::STANDARD
        .decode(base64_wav.trim())
        .map_err(|e| format!("Invalid base64 audio data: {e}"))?;

    if bytes.len() < 44 {
        return Err("WAV file too small".to_string());
    }
    if &bytes[0..4] != b"RIFF" || &bytes[8..12] != b"WAVE" {
        return Err("Invalid WAV header".to_string());
    }

    let voices_dir = get_voices_dir(&app);
    std::fs::create_dir_all(&voices_dir)
        .map_err(|e| format!("Failed to create voices directory: {e}"))?;

    let out_path = voices_dir.join(format!("{voice_id}.wav"));
    std::fs::write(&out_path, &bytes).map_err(|e| format!("Failed to write voice WAV: {e}"))?;

    Ok(out_path.to_string_lossy().to_string())
}
