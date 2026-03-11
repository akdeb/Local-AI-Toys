use std::env;
use std::ffi::OsStr;
use std::fs;
use std::path::PathBuf;
use std::process::Command;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

use crate::paths::get_venv_python;

const STT: &str = "mlx-community/whisper-large-v3-turbo-asr-fp16";
const LLM: &str = "mlx-community/Ministral-3-3B-Instruct-2512-4bit";
const TTS: &str = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-4bit";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelInfo {
    pub id: String,
    pub name: String,
    pub model_type: String,
    pub repo_id: String,
    pub downloaded: bool,
    pub size_estimate: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelStatus {
    pub models: Vec<ModelInfo>,
    pub all_downloaded: bool,
}

fn get_dir_size(path: &PathBuf) -> u64 {
    let mut total_size = 0;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.filter_map(Result::ok) {
            let entry_path = entry.path();
            if entry_path.is_file() {
                if let Ok(metadata) = entry.metadata() {
                    total_size += metadata.len();
                }
            } else if entry_path.is_dir() {
                total_size += get_dir_size(&entry_path);
            }
        }
    }
    total_size
}

fn format_size(bytes: u64) -> String {
    const KB: u64 = 1024;
    const MB: u64 = KB * 1024;
    const GB: u64 = MB * 1024;

    if bytes >= GB {
        format!("{:.2} GB", bytes as f64 / GB as f64)
    } else if bytes >= MB {
        format!("{:.2} MB", bytes as f64 / MB as f64)
    } else if bytes >= KB {
        format!("{:.2} KB", bytes as f64 / KB as f64)
    } else {
        format!("{} B", bytes)
    }
}

fn guess_model_type(repo_id: &str) -> String {
    let lower = repo_id.to_lowercase();
    if lower.contains("whisper") || lower.contains("stt") {
        "stt".to_string()
    } else if lower.contains("tts") || lower.contains("chatterbox") {
        "tts".to_string()
    } else {
        "llm".to_string()
    }
}

fn get_model_path(hf_cache: &PathBuf, repo_id: &str) -> Option<PathBuf> {
    let cache_name = format!("models--{}", repo_id.replace('/', "--"));
    let model_dir = hf_cache.join(&cache_name).join("snapshots");

    if model_dir.exists() {
        if let Ok(entries) = fs::read_dir(&model_dir) {
            for entry in entries.filter_map(Result::ok) {
                if entry.path().is_dir() {
                    return Some(entry.path());
                }
            }
        }
    }
    None
}

#[tauri::command]
pub async fn check_models_status(_app: AppHandle) -> Result<ModelStatus, String> {
    let home = env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let hf_cache = PathBuf::from(&home)
        .join(".cache")
        .join("huggingface")
        .join("hub");

    let mut models = vec![
        ModelInfo {
            id: "stt".to_string(),
            name: "Whisper Large V3 Turbo".to_string(),
            model_type: "stt".to_string(),
            repo_id: STT.to_string(),
            downloaded: false,
            size_estimate: None,
        },
        ModelInfo {
            id: "llm".to_string(),
            name: "Ministral 3 3B Instruct (2512)".to_string(),
            model_type: "llm".to_string(),
            repo_id: LLM.to_string(),
            downloaded: false,
            size_estimate: None,
        },
        ModelInfo {
            id: "tts".to_string(),
            name: "Qwen3-TTS 0.6B (4bit)".to_string(),
            model_type: "tts".to_string(),
            repo_id: TTS.to_string(),
            downloaded: false,
            size_estimate: None,
        },
    ];

    for model in &mut models {
        if let Some(path) = get_model_path(&hf_cache, &model.repo_id) {
            model.downloaded = true;
            let size = get_dir_size(&path);
            model.size_estimate = Some(format_size(size));
        }
    }

    let all_downloaded = models.iter().all(|m| m.downloaded);

    Ok(ModelStatus {
        models,
        all_downloaded,
    })
}

#[tauri::command]
pub async fn scan_local_models(_app: AppHandle) -> Result<Vec<ModelInfo>, String> {
    let home = env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let hf_cache = PathBuf::from(&home)
        .join(".cache")
        .join("huggingface")
        .join("hub");

    let mut models = Vec::new();

    if let Ok(entries) = fs::read_dir(&hf_cache) {
        for entry in entries.filter_map(Result::ok) {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }

            if let Some(name) = path.file_name().and_then(|n: &OsStr| n.to_str()) {
                if name.starts_with("models--") {
                    let without_prefix = name.trim_start_matches("models--");
                    if let Some((org, repo)) = without_prefix.split_once("--") {
                        let repo_id = format!("{}/{}", org, repo);
                        let repo_name = repo.to_string();

                        let model_type = guess_model_type(&repo_id);

                        let size_str = if let Some(model_path) = get_model_path(&hf_cache, &repo_id)
                        {
                            let size = get_dir_size(&model_path);
                            Some(format_size(size))
                        } else {
                            None
                        };

                        if let Some(size) = size_str {
                            models.push(ModelInfo {
                                id: repo_id.clone(),
                                name: repo_name,
                                model_type,
                                repo_id,
                                downloaded: true,
                                size_estimate: Some(size),
                            });
                        }
                    }
                }
            }
        }
    }

    models.sort_by(|a, b| a.name.cmp(&b.name));

    Ok(models)
}

#[tauri::command]
pub async fn download_model(app: AppHandle, repo_id: String) -> Result<String, String> {
    let venv_python = get_venv_python(&app);

    if !venv_python.exists() {
        return Err("Python environment not set up. Please complete setup first.".to_string());
    }

    app.emit(
        "model-download-progress",
        format!("Downloading {}...", repo_id),
    )
    .ok();

    let script = format!(
        r#"from huggingface_hub import snapshot_download; snapshot_download(repo_id="{}")"#,
        repo_id
    );

    let output = Command::new(venv_python.to_str().unwrap())
        .arg("-c")
        .arg(&script)
        .output()
        .map_err(|e| format!("Failed to download model: {}", e))?;

    if !output.status.success() {
        return Err(format!(
            "Failed to download model: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    app.emit(
        "model-download-progress",
        format!("Downloaded {} successfully!", repo_id),
    )
    .ok();
    Ok(format!("Model {} downloaded successfully", repo_id))
}

#[tauri::command]
pub async fn download_all_models(app: AppHandle) -> Result<String, String> {
    let models = vec![STT, LLM, TTS];

    for repo_id in models {
        download_model(app.clone(), repo_id.to_string()).await?;
    }

    Ok("All models downloaded successfully".to_string())
}
