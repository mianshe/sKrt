#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs;
use std::path::PathBuf;

fn storage_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let base = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("app_data_dir_failed:{e}"))?;
    let dir = base.join("native-storage");
    fs::create_dir_all(&dir).map_err(|e| format!("create_storage_dir_failed:{e}"))?;
    Ok(dir)
}

#[tauri::command]
fn native_storage_read_text(app: tauri::AppHandle, filename: String) -> Result<String, String> {
    let dir = storage_dir(&app)?;
    let path = dir.join(filename);
    if !path.exists() {
      return Ok("[]".to_string());
    }
    fs::read_to_string(path).map_err(|e| format!("read_storage_failed:{e}"))
}

#[tauri::command]
fn native_storage_write_text(app: tauri::AppHandle, filename: String, content: String) -> Result<(), String> {
    let dir = storage_dir(&app)?;
    let path = dir.join(filename);
    fs::write(path, content).map_err(|e| format!("write_storage_failed:{e}"))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            native_storage_read_text,
            native_storage_write_text
        ])
        .run(tauri::generate_context!())
        .expect("failed to run sKrt desktop client");
}
