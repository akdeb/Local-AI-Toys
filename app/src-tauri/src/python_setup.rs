use std::path::PathBuf;
use std::process::Command;

use tauri::AppHandle;

const EMBEDDED_REQUIREMENTS: &str =
    include_str!("../../../resources/python-backend/requirements.lock");

fn parse_requirement_specs(requirements: &str) -> Vec<String> {
    requirements
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .filter(|line| !line.starts_with('-'))
        .map(|line| line.to_string())
        .collect()
}

fn normalize_dependency_name(spec: &str) -> Option<String> {
    let trimmed = spec.split(';').next().unwrap_or("").trim();
    if trimmed.is_empty() {
        return None;
    }

    let before_at = trimmed.split('@').next().unwrap_or("").trim();
    if before_at.is_empty() {
        return None;
    }

    let mut end = before_at.len();
    for (idx, ch) in before_at.char_indices() {
        if matches!(ch, '=' | '<' | '>' | '!' | '~') {
            end = idx;
            break;
        }
    }

    let name = &before_at[..end];
    let name = name.split('[').next().unwrap_or("").trim();
    if name.is_empty() {
        None
    } else {
        Some(name.to_string())
    }
}

fn load_requirements() -> Result<&'static str, String> {
    if EMBEDDED_REQUIREMENTS.trim().is_empty() {
        return Err("Embedded requirements.lock is empty".to_string());
    }
    Ok(EMBEDDED_REQUIREMENTS)
}

pub fn pyproject_dependency_names(_app: &AppHandle) -> Result<Vec<String>, String> {
    let requirements = load_requirements()?;
    let specs = parse_requirement_specs(requirements);

    let mut out: Vec<String> = specs
        .into_iter()
        .filter_map(|dep| normalize_dependency_name(&dep))
        .collect();
    out.sort();
    out.dedup();
    Ok(out)
}

pub fn install_python_deps(_app: &AppHandle, pip_path: PathBuf) -> Result<String, String> {
    if !pip_path.exists() {
        return Err("Virtual environment not found. Please create it first.".to_string());
    }

    let _ = Command::new(pip_path.to_str().unwrap())
        .arg("install")
        .arg("--upgrade")
        .arg("pip")
        .output();

    let requirements = load_requirements()?;
    let specs = parse_requirement_specs(requirements);
    if specs.is_empty() {
        return Err("No dependencies found in requirements.lock".to_string());
    }

    // Install mlx-audio without deps to avoid resolver conflicts with mlx family versions.
    let mut mlx_audio_spec: Option<String> = None;
    let mut rest: Vec<String> = Vec::new();
    for dep in specs {
        if dep.starts_with("mlx-audio") {
            if mlx_audio_spec.is_some() {
                return Err("Multiple mlx-audio entries found in requirements.lock".to_string());
            }
            mlx_audio_spec = Some(dep);
        } else {
            rest.push(dep);
        }
    }

    if let Some(spec) = mlx_audio_spec {
        let output = Command::new(pip_path.to_str().unwrap())
            .args([
                "install",
                "--upgrade",
                "--force-reinstall",
                "--no-deps",
                "--prefer-binary",
                &spec,
            ])
            .output()
            .map_err(|e| format!("Failed to install mlx-audio: {}", e))?;

        if !output.status.success() {
            return Err(format!(
                "Failed to install mlx-audio: {}",
                String::from_utf8_lossy(&output.stderr)
            ));
        }
    }

    let mut cmd = Command::new(pip_path.to_str().unwrap());
    cmd.arg("install")
        .arg("--upgrade")
        .arg("--force-reinstall")
        .arg("--prefer-binary");
    for dep in rest {
        cmd.arg(dep);
    }

    let output = cmd
        .output()
        .map_err(|e| format!("Failed to install deps: {}", e))?;

    if !output.status.success() {
        return Err(format!(
            "Failed to install dependencies: {}",
            String::from_utf8_lossy(&output.stderr)
        ));
    }

    Ok("Dependencies installed successfully".to_string())
}
