//! Canal assinado definido no overlay de build; credencial somente no cofre nativo.
use super::update_enrollment::{InstallationVault, Vault};
use super::{BackendState, Connection};
use reqwest::header::{HeaderMap, HeaderValue, AUTHORIZATION};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Clone, Serialize)]
pub struct UpdateState {
    phase: String,
    current_version: String,
    auto_check: bool,
    configured: bool,
    last_check_at: Option<String>,
    available: Option<Value>,
    blocked_reason: Option<String>,
    progress: Option<Value>,
    backup_path_label: Option<String>,
    error: Option<Value>,
    installed_version: String,
    available_version: Option<String>,
    notes: Option<String>,
    channel: Option<String>,
    enrolled: bool,
    last_error: Option<Value>,
}
impl UpdateState {
    fn initial(current_version: String, auto_check: bool, configured: bool) -> Self {
        Self {
            phase: if configured { "idle" } else { "disabled" }.into(),
            installed_version: current_version.clone(),
            current_version,
            auto_check,
            configured,
            last_check_at: None,
            available: None,
            blocked_reason: None,
            progress: None,
            backup_path_label: None,
            error: None,
            available_version: None,
            notes: None,
            channel: None,
            enrolled: false,
            last_error: None,
        }
    }
    fn transition(&mut self, phase: &str) {
        self.phase = phase.into();
        self.error = None;
        self.last_error = None;
        self.blocked_reason = None;
        if phase != "downloading" {
            self.progress = None;
        }
    }
    fn fail(&mut self, code: &str) {
        self.transition("error");
        self.error = Some(
            json!({"code":code,"message":"Atualização não concluída; preserve seus dados e tente novamente."}),
        );
        self.last_error = self.error.clone();
    }
}
struct Model {
    state: UpdateState,
    update: Option<Update>,
}
pub struct UpdaterState {
    model: Mutex<Model>,
    operation: tauri::async_runtime::Mutex<()>,
    directory: PathBuf,
    installing: AtomicBool,
    verifying: AtomicBool,
    channel: Option<String>,
    local_test: bool,
    vault: InstallationVault,
}
impl UpdaterState {
    pub fn installing(&self) -> bool {
        self.installing.load(Ordering::Acquire)
    }
}
struct InstallationGuard<'a>(&'a AtomicBool);
impl Drop for InstallationGuard<'_> {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}
fn should_auto_check(configured: bool, auto_check: bool) -> bool {
    configured && auto_check
}
fn preference(directory: &std::path::Path) -> bool {
    std::fs::read(directory.join("preferences.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .and_then(|v| v["auto_check"].as_bool())
        .unwrap_or(true)
}
fn save_preference(directory: &std::path::Path, enabled: bool) -> std::io::Result<()> {
    std::fs::create_dir_all(directory)?;
    let temporary = directory.join(format!("preferences-{}.tmp", uuid::Uuid::new_v4()));
    let result = std::fs::write(&temporary, json!({"auto_check":enabled}).to_string())
        .and_then(|_| std::fs::rename(&temporary, directory.join("preferences.json")));
    if result.is_err() {
        let _ = std::fs::remove_file(temporary);
    }
    result
}
fn snapshot(app: &AppHandle) -> UpdateState {
    app.state::<UpdaterState>()
        .model
        .lock()
        .unwrap()
        .state
        .clone()
}
fn change(app: &AppHandle, apply: impl FnOnce(&mut Model)) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    let state = {
        let mut model = managed.model.lock().unwrap();
        apply(&mut model);
        model.state.available_version = model
            .state
            .available
            .as_ref()
            .and_then(|v| v["version"].as_str())
            .map(str::to_owned);
        model.state.notes = model
            .state
            .available
            .as_ref()
            .and_then(|v| v["notes"].as_str())
            .map(str::to_owned);
        model.state.last_error = model.state.error.clone();
        model.state.clone()
    };
    let _ = app.emit("update://state", &state);
    state
}
fn phase(app: &AppHandle, name: &str) {
    change(app, |m| {
        m.state.transition(name);
    });
}
fn failure(app: &AppHandle, code: &str) -> UpdateState {
    change(app, |m| {
        m.state.fail(code);
    })
}
fn blocked(app: &AppHandle, reason: &str) -> UpdateState {
    change(app, |m| {
        m.state.phase = "blocked".into();
        m.state.blocked_reason = Some(reason.into());
        m.state.error = None;
    })
}
pub fn newer(candidate: &str, current: &str) -> bool {
    match (
        semver::Version::parse(candidate),
        semver::Version::parse(current),
    ) {
        (Ok(a), Ok(b)) => a.cmp_precedence(&b).is_gt(),
        _ => false,
    }
}
fn local_url(raw: &str) -> bool {
    reqwest::Url::parse(raw).is_ok_and(|u| {
        u.scheme() == "http"
            && u.host_str() == Some("127.0.0.1")
            && u.username().is_empty()
            && u.password().is_none()
    })
}
fn configured(config: Option<&Value>, channel: Option<&str>, local_test: bool) -> bool {
    config.is_some_and(|c| {
        c["pubkey"].as_str().is_some_and(|k| !k.is_empty())
            && c["requireSignedVersion"] == true
            && c["dangerousAcceptInvalidCerts"] != true
            && c["dangerousAcceptInvalidHostnames"] != true
            && c["endpoints"].as_array().is_some_and(|a| {
                // Uma única origem impede fallback que enviaria a credencial a outro host.
                a.len() == 1
                    && a.iter().all(|v| {
                        v.as_str().is_some_and(|raw| {
                            if local_test
                                && c["dangerousInsecureTransportProtocol"] == true
                                && local_url(raw)
                            {
                                return channel.is_none_or(|c| c == "test");
                            }
                            c["dangerousInsecureTransportProtocol"] != true
                                && channel.is_some_and(|channel| valid_endpoint(raw, channel))
                        })
                    })
            })
    })
}
fn valid_endpoint(raw: &str, channel: &str) -> bool {
    matches!(channel, "test" | "production")
        && reqwest::Url::parse(raw).is_ok_and(|u| {
            u.scheme() == "https"
                && u.host_str().is_some()
                && !u
                    .host_str()
                    .is_some_and(|h| matches!(h, "127.0.0.1" | "localhost" | "[::1]"))
                && u.username().is_empty()
                && u.password().is_none()
                && u.query().is_none()
                && u.fragment().is_none()
                && u.path() == format!("/{channel}/latest.json")
        })
}
fn channel_version(version: &str, channel: Option<&str>, local_test: bool) -> bool {
    if local_test {
        return semver::Version::parse(version).is_ok();
    }
    let Ok(version) = semver::Version::parse(version) else {
        return false;
    };
    if !version.build.is_empty() {
        return false;
    }
    match channel {
        Some("production") => version.pre.is_empty(),
        Some("test") => version.pre.as_str().strip_prefix("test.").is_some_and(|n| {
            !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()) && n.parse::<u64>().is_ok()
        }),
        _ => false,
    }
}
fn build_channel(app: &AppHandle) -> Option<String> {
    app.config()
        .plugins
        .0
        .get("horusUpdater")
        .and_then(|v| v["channel"].as_str())
        .map(str::to_owned)
}
fn local_test_build(app: &AppHandle) -> bool {
    // A exceção não pode ser habilitada no identificador operacional.
    app.config().identifier == "com.horus.installer-test"
        && app.config().plugins.0.get("updater").is_some_and(|c| {
            c["dangerousInsecureTransportProtocol"] == true
                && c["endpoints"]
                    .as_array()
                    .is_some_and(|a| a.len() == 1 && a[0].as_str().is_some_and(local_url))
        })
}
fn authorization(token: &str) -> Result<HeaderMap, &'static str> {
    if !super::update_enrollment::valid_token(token) {
        return Err("enrollment_invalid");
    }
    let mut value =
        HeaderValue::from_str(&format!("Bearer {token}")).map_err(|_| "enrollment_invalid")?;
    value.set_sensitive(true);
    let mut headers = HeaderMap::new();
    headers.insert(AUTHORIZATION, value);
    Ok(headers)
}
fn allowed_download(
    endpoint: &str,
    download: &str,
    version: &str,
    channel: Option<&str>,
    local_test: bool,
) -> bool {
    let (Ok(base), Ok(url)) = (reqwest::Url::parse(endpoint), reqwest::Url::parse(download)) else {
        return false;
    };
    if url.origin() != base.origin()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return false;
    }
    if local_test {
        return local_url(download);
    }
    channel.is_some_and(|c| {
        valid_endpoint(endpoint, c)
            && channel_version(version, channel, false)
            && url.path() == format!("/{c}/releases/{version}/HORUS_{version}_x64-setup.exe")
    })
}
// Somente overlays locais de E2E podem fixar o sandbox: o NSIS pode reiniciar
// via shell sem preservar variáveis do processo que iniciou o ensaio.
pub fn e2e_root(app: &AppHandle) -> Option<PathBuf> {
    if !local_test_build(app)
        || !configured(
            app.config().plugins.0.get("updater"),
            build_channel(app).as_deref(),
            true,
        )
    {
        return None;
    }
    let raw = app
        .config()
        .plugins
        .0
        .get("horusUpdaterTest")?
        .get("root")?
        .as_str()?;
    e2e_path(raw)
}
fn e2e_path(raw: &str) -> Option<PathBuf> {
    let original = PathBuf::from(raw);
    if !original.is_absolute() {
        return None;
    }
    let root = original.canonicalize().ok()?;
    let temp = std::env::temp_dir().canonicalize().ok()?;
    if root.starts_with(temp)
        && root
            .file_name()?
            .to_str()?
            .starts_with("horus-updater-e2e-")
    {
        // canonicalize no Windows acrescenta \\?\, que não deve atravessar
        // HORUS_DATA_DIR: Python/SQLite transformam isso em URI com autoridade.
        Some(original)
    } else {
        None
    }
}
pub fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let channel = build_channel(app.handle());
    let local_test = local_test_build(app.handle());
    let enabled = configured(
        app.config().plugins.0.get("updater"),
        channel.as_deref(),
        local_test,
    );
    let vault = InstallationVault(format!(
        "{}.updater.{}",
        app.config().identifier,
        channel.as_deref().unwrap_or("local-test")
    ));
    // Não acessar credenciais operacionais quando o build não tem canal.
    let enrollment = if enabled && !local_test {
        vault.read()
    } else {
        Ok(None)
    };
    let enrolled = enrollment
        .as_ref()
        .ok()
        .and_then(|v| v.as_ref())
        .is_some_and(|t| super::update_enrollment::valid_token(t));
    let directory = e2e_root(app.handle())
        .map(|p| p.join("data"))
        .or_else(|| std::env::var_os("HORUS_DATA_DIR").map(PathBuf::from))
        .unwrap_or(app.path().app_local_data_dir()?.join("workspace"))
        .join("updater");
    let auto_check = preference(&directory);
    let pending = std::fs::read(directory.join("pending-health.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .filter(|_| enabled);
    if enabled {
        app.handle()
            .plugin(tauri_plugin_updater::Builder::new().build())?;
    }
    app.manage(UpdaterState {
        channel: channel.clone(),
        local_test,
        vault,
        directory,
        installing: AtomicBool::new(false),
        verifying: AtomicBool::new(pending.is_some()),
        operation: Default::default(),
        model: Mutex::new(Model {
            state: {
                let mut state = UpdateState::initial(
                    app.package_info().version.to_string(),
                    auto_check,
                    enabled,
                );
                state.channel = channel;
                state.enrolled = enrolled;
                if enrollment.is_err() {
                    state.fail("credential_unavailable");
                } else if enrollment.as_ref().ok().and_then(|v| v.as_ref()).is_some() && !enrolled {
                    state.fail("enrollment_invalid");
                } else if !enabled && app.config().plugins.0.contains_key("updater") {
                    state.fail("configuration_invalid");
                }
                state
            },
            update: None,
        }),
    });
    if let Some(pending) = pending {
        phase(app.handle(), "installing");
        let handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            verify_restart(handle, pending).await;
        });
    } else if should_auto_check(enabled && (enrolled || local_test), auto_check) {
        let handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            update_check(handle).await;
        });
    }
    Ok(())
}
#[tauri::command(rename_all = "snake_case")]
pub fn update_get_state(app: AppHandle) -> UpdateState {
    snapshot(&app)
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_enroll(app: AppHandle, token: String) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    let Ok(_guard) = managed.operation.try_lock() else {
        return snapshot(&app);
    };
    if !snapshot(&app).configured || managed.local_test {
        return failure(&app, "configuration_invalid");
    }
    if let Err(code) = super::update_enrollment::enroll(&managed.vault, &token) {
        return failure(&app, code);
    }
    change(&app, |m| {
        m.update = None;
        m.state.available = None;
        m.state.enrolled = true;
        m.state.transition("idle");
    })
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_clear_enrollment(app: AppHandle) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    let Ok(_guard) = managed.operation.try_lock() else {
        return snapshot(&app);
    };
    if !snapshot(&app).configured || managed.local_test {
        return failure(&app, "configuration_invalid");
    }
    if let Err(code) = managed.vault.clear() {
        return failure(&app, code);
    }
    change(&app, |m| {
        m.update = None;
        m.state.available = None;
        m.state.enrolled = false;
        m.state.transition("idle");
    })
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_set_auto_check(app: AppHandle, enabled: bool) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    if save_preference(&managed.directory, enabled).is_err() {
        return failure(&app, "install_failed");
    }
    change(&app, |m| m.state.auto_check = enabled)
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_check(app: AppHandle) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    if managed.verifying.load(Ordering::Acquire) {
        return snapshot(&app);
    }
    let Ok(_guard) = managed.operation.try_lock() else {
        return snapshot(&app);
    };
    if !snapshot(&app).configured {
        return snapshot(&app);
    }
    let headers = if managed.local_test {
        HeaderMap::new()
    } else {
        match managed.vault.read() {
            Ok(Some(token)) => match authorization(&token) {
                Ok(headers) => headers,
                Err(code) => return failure(&app, code),
            },
            Ok(None) => {
                change(&app, |m| m.state.enrolled = false);
                return failure(&app, "enrollment_required");
            }
            Err(code) => return failure(&app, code),
        }
    };
    phase(&app, "checking");
    change(&app, |m| {
        m.state.available = None;
        m.update = None;
        m.state.last_check_at = Some(timestamp());
    });
    // O comparador permite inspecionar também manifesto antigo; a guarda abaixo nunca o disponibiliza.
    let builder = app
        .updater_builder()
        // O plugin 2.14.1 aplica headers tanto no check quanto em Update::download.
        .headers(headers)
        .timeout(Duration::from_secs(20))
        .no_proxy()
        .configure_client(|c| c.redirect(reqwest::redirect::Policy::none()))
        .version_comparator(|_, _| true);
    let result = match builder.build() {
        Ok(updater) => updater.check().await,
        Err(e) => Err(e),
    };
    match result {
        Ok(Some(update)) => {
            if !newer(&update.version, &snapshot(&app).current_version) {
                return failure(&app, "downgrade_refused");
            }
            let endpoint = app.config().plugins.0["updater"]["endpoints"][0]
                .as_str()
                .unwrap_or("");
            if !allowed_download(
                endpoint,
                update.download_url.as_str(),
                &update.version,
                managed.channel.as_deref(),
                managed.local_test,
            ) {
                return failure(&app, "manifest_invalid");
            }
            change(&app, |m| {
                m.state.phase = "available".into();
                m.state.last_check_at = Some(timestamp());
                m.state.available = Some(
                    json!({"version":update.version,"notes":update.body.clone().unwrap_or_default(),
                    "pub_date":update.raw_json.get("pub_date").cloned().unwrap_or(Value::Null)}),
                );
                m.update = Some(update);
            })
        }
        Ok(None) => change(&app, |m| {
            m.state.phase = "up_to_date".into();
            m.state.available = None;
            m.update = None;
            m.state.last_check_at = Some(timestamp());
        }),
        Err(error) => failure(&app, manifest_error(&error)),
    }
}
fn timestamp() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap()
}
#[derive(Deserialize)]
struct Activity {
    busy: bool,
}
async fn backend(
    client: &reqwest::Client,
    connection: &Connection,
    method: reqwest::Method,
    route: &str,
) -> Result<Value, ()> {
    client
        .request(method, format!("{}{}", connection.base_url, route))
        .bearer_auth(&connection.token)
        .send()
        .await
        .map_err(|_| ())?
        .error_for_status()
        .map_err(|_| ())?
        .json()
        .await
        .map_err(|_| ())
}
async fn activity(client: &reqwest::Client, connection: &Connection) -> Result<bool, ()> {
    let value = backend(
        client,
        connection,
        reqwest::Method::GET,
        "/v1/update/activity",
    )
    .await?;
    serde_json::from_value::<Activity>(value)
        .map(|a| a.busy)
        .map_err(|_| ())
}
fn confirm(confirmed: &str, available: &str, current: &str) -> Result<(), &'static str> {
    if confirmed != available {
        return Err("install_failed");
    }
    if !newer(available, current) {
        return Err("downgrade_refused");
    }
    Ok(())
}
trait GuardBackend {
    fn activity(&self) -> impl std::future::Future<Output = Result<bool, &'static str>> + Send;
    fn reserve(&self) -> impl std::future::Future<Output = Result<(), &'static str>> + Send;
    fn backup(&self) -> impl std::future::Future<Output = Result<String, &'static str>> + Send;
}
async fn prepare(backend: &impl GuardBackend, reserved: &mut bool) -> Result<String, &'static str> {
    if backend.activity().await? {
        return Err("job_running");
    }
    backend.reserve().await?;
    *reserved = true;
    if backend.activity().await? {
        return Err("job_running");
    }
    backend.backup().await
}
struct LiveBackend<'a> {
    app: &'a AppHandle,
    client: &'a reqwest::Client,
    connection: &'a Connection,
}
impl GuardBackend for LiveBackend<'_> {
    async fn activity(&self) -> Result<bool, &'static str> {
        activity(self.client, self.connection)
            .await
            .map_err(|_| "backend_unavailable")
    }
    async fn reserve(&self) -> Result<(), &'static str> {
        match self
            .client
            .post(format!(
                "{}/v1/update/reservation",
                self.connection.base_url
            ))
            .bearer_auth(&self.connection.token)
            .send()
            .await
        {
            Ok(response) if response.status() == reqwest::StatusCode::CONFLICT => {
                Err("job_running")
            }
            Ok(response) if response.status().is_success() => {
                match response.json::<Value>().await {
                    Ok(value) if value["reserved"] == true => Ok(()),
                    _ => Err("backend_unavailable"),
                }
            }
            _ => Err("backend_unavailable"),
        }
    }
    async fn backup(&self) -> Result<String, &'static str> {
        phase(self.app, "backing_up");
        let value = backend(
            self.client,
            self.connection,
            reqwest::Method::POST,
            "/v1/update/backup",
        )
        .await
        .map_err(|_| "backup_failed")?;
        if value["ok"] != true {
            return Err("backup_failed");
        }
        value["label"]
            .as_str()
            .map(String::from)
            .ok_or("backup_failed")
    }
}
#[tauri::command(rename_all = "snake_case")]
pub async fn update_install(app: AppHandle, confirmed_version: String) -> UpdateState {
    let managed = app.state::<UpdaterState>();
    if managed.verifying.load(Ordering::Acquire) {
        return snapshot(&app);
    }
    let Ok(_guard) = managed.operation.try_lock() else {
        return snapshot(&app);
    };
    if !snapshot(&app).configured {
        return snapshot(&app);
    }
    let update = managed.model.lock().unwrap().update.clone();
    let Some(mut update) = update else {
        return failure(&app, "install_failed");
    };
    // Releitura antes de qualquer reserva: limpar/trocar a credencial invalida o cache.
    if !managed.local_test {
        update.headers = match managed.vault.read() {
            Ok(Some(token)) => match authorization(&token) {
                Ok(headers) => headers,
                Err(code) => return failure(&app, code),
            },
            Ok(None) => {
                change(&app, |m| m.state.enrolled = false);
                return failure(&app, "enrollment_required");
            }
            Err(code) => return failure(&app, code),
        };
    }
    if let Err(code) = confirm(
        &confirmed_version,
        &update.version,
        &snapshot(&app).current_version,
    ) {
        return failure(&app, code);
    }
    managed.installing.store(true, Ordering::Release);
    let _installation = InstallationGuard(&managed.installing);
    let connection = app
        .state::<BackendState>()
        .0
        .lock()
        .unwrap()
        .as_ref()
        .map(|b| b.connection.clone());
    let Some(connection) = connection else {
        return blocked(&app, "backend_unavailable");
    };
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(60))
        .build()
        .unwrap();
    let mut reserved = false;
    let preparation = prepare(
        &LiveBackend {
            app: &app,
            client: &client,
            connection: &connection,
        },
        &mut reserved,
    )
    .await;
    let result = match preparation {
        Ok(label) => {
            change(&app, |m| m.state.backup_path_label = Some(label));
            install_reserved(&app, &client, &connection, update).await
        }
        Err("job_running") => blocked(&app, "job_running"),
        Err("backend_unavailable") => blocked(&app, "backend_unavailable"),
        Err(code) => failure(&app, code),
    };
    // Se instalou no Windows o plugin encerrou o processo. Em toda falha, libera a reserva.
    if reserved {
        let _ = backend(
            &client,
            &connection,
            reqwest::Method::DELETE,
            "/v1/update/reservation",
        )
        .await;
    }
    result
}
trait PayloadBackend {
    fn download(&self) -> impl std::future::Future<Output = Result<Vec<u8>, &'static str>> + Send;
    fn cache(&self, bytes: &[u8]) -> Result<(), &'static str>;
    fn shutdown(&self) -> impl std::future::Future<Output = Result<(), &'static str>> + Send;
    fn install(&self, bytes: &[u8]) -> Result<(), &'static str>;
}
async fn finish_update(steps: &impl PayloadBackend) -> Result<(), &'static str> {
    let bytes = steps.download().await?; // O plugin verifica assinatura e versão antes de devolver bytes.
    steps.cache(&bytes)?;
    steps.shutdown().await?;
    steps.install(&bytes)
}
struct LivePayload<'a> {
    backend: LiveBackend<'a>,
    update: Update,
}
impl PayloadBackend for LivePayload<'_> {
    async fn download(&self) -> Result<Vec<u8>, &'static str> {
        let app = self.backend.app;
        phase(app, "downloading");
        let mut downloaded = 0u64;
        self.update
            .download(
                |chunk, total| {
                    downloaded += chunk as u64;
                    change(app, |m| {
                        m.state.progress = Some(
                            json!({"phase":"downloading","downloaded":downloaded,"total":total}),
                        )
                    });
                },
                || {},
            )
            .await
            .map_err(|e| download_error(&e))
    }
    fn cache(&self, bytes: &[u8]) -> Result<(), &'static str> {
        let folder = self
            .backend
            .app
            .state::<UpdaterState>()
            .directory
            .join("installers")
            .join(&self.update.version);
        std::fs::create_dir_all(&folder)
            .and_then(|_| std::fs::write(folder.join("setup.exe"), bytes))
            .and_then(|_| std::fs::write(folder.join("setup.exe.sig"), &self.update.signature))
            .map_err(|_| "install_failed")
    }
    async fn shutdown(&self) -> Result<(), &'static str> {
        let app = self.backend.app;
        phase(app, "installing");
        let owned = app.state::<BackendState>().0.lock().unwrap().take();
        let Some(mut owned) = owned else {
            return Err("backend_unavailable");
        };
        backend(
            self.backend.client,
            self.backend.connection,
            reqwest::Method::POST,
            "/v1/shutdown",
        )
        .await
        .map_err(|_| "install_failed")?;
        let shutdown = tauri::async_runtime::spawn_blocking(move || {
            let until = std::time::Instant::now() + Duration::from_secs(15);
            while std::time::Instant::now() < until {
                if let Some(status) = owned.child.try_wait().map_err(|_| ())? {
                    return if status.success() { Ok(()) } else { Err(()) };
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(())
        })
        .await;
        if matches!(shutdown, Ok(Ok(()))) {
            Ok(())
        } else {
            Err("install_failed")
        }
    }
    fn install(&self, bytes: &[u8]) -> Result<(), &'static str> {
        let directory = &self.backend.app.state::<UpdaterState>().directory;
        let receipt = directory.join("pending-health.json");
        std::fs::write(
            &receipt,
            json!({"target_version":self.update.version,
                "backup_path_label":snapshot(self.backend.app).backup_path_label})
            .to_string(),
        )
        .map_err(|_| "install_failed")?;
        self.update.install(bytes).map_err(|_| {
            let _ = std::fs::remove_file(receipt);
            "install_failed"
        })
    }
}
fn finish_health(
    directory: &std::path::Path,
    target: &str,
    current: &str,
    health: Option<&Value>,
) -> std::io::Result<bool> {
    let healthy = target == current
        && health.is_some_and(|h| {
            h["ok"] == true
                && h["integrity_check"] == "ok"
                && h["schema_version"].as_u64().is_some_and(|v| v > 0)
        });
    std::fs::write(directory.join("last-update-result.json"),json!({"target_version":target,"current_version":current,"healthy":healthy,"checked_at":timestamp()}).to_string())?;
    if healthy {
        std::fs::remove_file(directory.join("pending-health.json"))?;
    }
    Ok(healthy)
}
async fn verify_restart(app: AppHandle, pending: Value) {
    let managed = app.state::<UpdaterState>();
    let _guard = managed.operation.lock().await;
    let connection = super::backend_connection(app.clone(), app.state::<BackendState>()).await;
    let health = if let Ok(connection) = connection {
        if let Ok(client) = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(30))
            .build()
        {
            backend(
                &client,
                &connection,
                reqwest::Method::GET,
                "/v1/update/health",
            )
            .await
            .ok()
        } else {
            None
        }
    } else {
        None
    };
    let target = pending["target_version"].as_str().unwrap_or("");
    let healthy = finish_health(
        &managed.directory,
        target,
        &snapshot(&app).current_version,
        health.as_ref(),
    )
    .unwrap_or(false);
    managed.verifying.store(false, Ordering::Release);
    change(&app, |m| {
        apply_restart_result(&mut m.state, &pending, healthy)
    });
}
fn apply_restart_result(state: &mut UpdateState, pending: &Value, healthy: bool) {
    if healthy {
        state.transition("idle");
    } else {
        state.backup_path_label = pending["backup_path_label"]
            .as_str()
            .filter(|label| safe_backup_label(label))
            .map(str::to_owned);
        state.fail("startup_failed");
    }
}

async fn install_reserved(
    app: &AppHandle,
    client: &reqwest::Client,
    connection: &Connection,
    update: Update,
) -> UpdateState {
    match finish_update(&LivePayload {
        backend: LiveBackend {
            app,
            client,
            connection,
        },
        update,
    })
    .await
    {
        Ok(()) => snapshot(app),
        Err("backend_unavailable") => blocked(app, "backend_unavailable"),
        Err(code) => failure(app, code),
    }
}
fn safe_backup_label(label: &str) -> bool {
    label
        .strip_prefix("pre-update-")
        .and_then(|s| s.strip_suffix(".zip"))
        .is_some_and(|id| uuid::Uuid::parse_str(id).is_ok())
}
fn manifest_error(error: &tauri_plugin_updater::Error) -> &'static str {
    use tauri_plugin_updater::Error;
    match error {
        Error::Serialization(_)
        | Error::Semver(_)
        | Error::UrlParse(_)
        | Error::TargetNotFound(_)
        | Error::TargetsNotFound(_) => "manifest_invalid",
        Error::Reqwest(e) if e.is_decode() => "manifest_invalid",
        // ReleaseNotFound also means a non-success HTTP status, not invalid JSON.
        Error::Reqwest(_) | Error::Network(_) | Error::ReleaseNotFound => "network",
        _ => download_error(error),
    }
}
fn download_error(error: &tauri_plugin_updater::Error) -> &'static str {
    match error {
        tauri_plugin_updater::Error::Minisign(_)
        | tauri_plugin_updater::Error::Base64(_)
        | tauri_plugin_updater::Error::SignatureUtf8(_)
        | tauri_plugin_updater::Error::SignedVersionMismatch { .. }
        | tauri_plugin_updater::Error::MissingSignedVersion => "signature_invalid",
        tauri_plugin_updater::Error::Reqwest(_) | tauri_plugin_updater::Error::Network(_) => {
            "network"
        }
        _ => "install_failed",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn success_receipt_requires_new_version_and_authenticated_integrity_health() {
        let root =
            std::env::temp_dir().join(format!("horus-updater-health-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&root).unwrap();
        std::fs::write(root.join("pending-health.json"), "{}").unwrap();
        let good = json!({"ok":true,"integrity_check":"ok","schema_version":9});
        let corrupt = json!({"ok":true,"integrity_check":"corrupt","schema_version":9});
        let invalid_schema = json!({"ok":true,"integrity_check":"ok","schema_version":"invalid"});
        let missing_schema = json!({"ok":true,"integrity_check":"ok"});
        for (current, health) in [
            ("1.0.0", Some(&good)),
            ("1.1.0", None),
            ("1.1.0", Some(&corrupt)),
            ("1.1.0", Some(&invalid_schema)),
            ("1.1.0", Some(&missing_schema)),
        ] {
            assert!(!finish_health(&root, "1.1.0", current, health).unwrap());
            assert!(root.join("pending-health.json").exists());
            let mut state = UpdateState::initial(current.into(), true, true);
            apply_restart_result(&mut state, &json!({"target_version":"1.1.0"}), false);
            assert_eq!(state.error.unwrap()["code"], "startup_failed");
            let stored: Value = serde_json::from_slice(
                &std::fs::read(root.join("last-update-result.json")).unwrap(),
            )
            .unwrap();
            assert_eq!(stored["healthy"], false);
        }
        assert!(finish_health(&root, "1.1.0", "1.1.0", Some(&good)).unwrap());
        assert!(!root.join("pending-health.json").exists());
        std::fs::remove_file(root.join("last-update-result.json")).unwrap();
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn e2e_path_keeps_sqlite_compatible_lexical_path_and_rejects_other_roots() {
        let root = std::env::temp_dir().join(format!("horus-updater-e2e-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir(&root).unwrap();
        let selected = e2e_path(root.to_str().unwrap()).unwrap();
        assert_eq!(selected, root);
        assert!(!selected.to_string_lossy().starts_with(r"\\?\"));
        assert!(e2e_path("relative-path").is_none());
        assert!(e2e_path(std::env::temp_dir().to_str().unwrap()).is_none());
        std::fs::remove_dir(root).unwrap();
    }
    #[test]
    fn preference_survives_restart_and_auto_check_requires_both_flags() {
        let directory =
            std::env::temp_dir().join(format!("horus-updater-test-{}", uuid::Uuid::new_v4()));
        assert!(preference(&directory));
        save_preference(&directory, false).unwrap();
        assert!(!preference(&directory));
        save_preference(&directory, true).unwrap();
        assert!(preference(&directory));
        std::fs::remove_dir_all(directory).unwrap();
        assert!(should_auto_check(true, true));
        for (c, a) in [(false, true), (true, false), (false, false)] {
            assert!(!should_auto_check(c, a));
        }
    }
    #[test]
    fn install_guard_is_released_on_error_return() {
        let flag = AtomicBool::new(true);
        {
            let _guard = InstallationGuard(&flag);
            assert!(flag.load(Ordering::Acquire));
        }
        assert!(!flag.load(Ordering::Acquire));
    }
    #[test]
    fn state_defaults_and_transitions_match_frontend_contract() {
        let disabled = UpdateState::initial("1.0.0".into(), true, false);
        assert_eq!(disabled.phase, "disabled");
        assert!(!disabled.configured);
        let mut state = UpdateState::initial("1.0.0".into(), true, true);
        assert_eq!(state.phase, "idle");
        state.transition("downloading");
        state.progress = Some(json!({"downloaded":7,"total":10}));
        state.fail("signature_invalid");
        assert_eq!(state.phase, "error");
        assert!(state.progress.is_none());
        assert_eq!(state.error.as_ref().unwrap()["code"], "signature_invalid");
        state.transition("checking");
        assert!(state.error.is_none());
        let wire = serde_json::to_value(&state).unwrap();
        assert_eq!(wire.as_object().unwrap().len(), 16);
        assert_eq!(wire["installed_version"], "1.0.0");
        assert_eq!(wire["enrolled"], false);
        assert!(wire["channel"].is_null());
        assert_eq!(wire["current_version"], "1.0.0");
        assert!(wire["last_check_at"].is_null());
    }
    #[test]
    fn semver_guard() {
        assert!(newer("1.2.0", "1.1.9"));
        assert!(newer("1.0.0", "1.0.0-rc.1"));
        for v in ["1.0.0", "0.9.0", "1.0.0+build.2", "invalid"] {
            assert!(!newer(v, "1.0.0"));
        }
    }
    #[test]
    fn channel_is_only_explicit_loopback() {
        assert!(!configured(None, None, false));
        assert!(!configured(Some(&json!({})), None, false));
        let mut c = json!({"pubkey":"test","endpoints":["http://127.0.0.1:19420/latest.json"],"dangerousInsecureTransportProtocol":true,"requireSignedVersion":true});
        assert!(configured(Some(&c), None, true));
        assert!(!configured(Some(&c), None, false));
        assert!(!configured(Some(&c), Some("production"), true));
        for url in [
            "http://evil.test",
            "http://127.0.0.1.evil.test",
            "http://secret@127.0.0.1",
            "https://127.0.0.1",
        ] {
            c["endpoints"] = json!([url]);
            assert!(!configured(Some(&c), None, true));
        }
    }
    #[test]
    fn https_channels_and_downloads_are_confined_to_the_configured_origin_and_route() {
        let endpoint = "https://updates.example.test/test/latest.json";
        let mut c = json!({"pubkey":"fixture","endpoints":[endpoint],"requireSignedVersion":true});
        assert!(configured(Some(&c), Some("test"), false));
        for channel in [None, Some("stable"), Some("production")] {
            assert!(!configured(Some(&c), channel, false));
        }
        for bad in [
            "http://updates.example.test/test/latest.json",
            "https://secret@updates.example.test/test/latest.json",
            "https://updates.example.test/test/latest.json?secret=1",
            "https://updates.example.test/production/latest.json",
            "https://localhost/test/latest.json",
        ] {
            c["endpoints"] = json!([bad]);
            assert!(!configured(Some(&c), Some("test"), false));
        }
        let good = "https://updates.example.test/test/releases/1.1.0-test.1/HORUS_1.1.0-test.1_x64-setup.exe";
        assert!(allowed_download(
            endpoint,
            good,
            "1.1.0-test.1",
            Some("test"),
            false
        ));
        for bad in [
            good.replace("https:", "http:"),
            good.replace("updates.example.test", "evil.example.test"),
            good.replace("/test/", "/production/"),
            format!("{good}?credential=1"),
        ] {
            assert!(!allowed_download(
                endpoint,
                &bad,
                "1.1.0-test.1",
                Some("test"),
                false
            ));
        }
    }
    #[test]
    fn bearer_header_is_sensitive_and_invalid_input_has_no_header() {
        let token = "a".repeat(64);
        let headers = authorization(&token).unwrap();
        assert_eq!(headers[AUTHORIZATION], format!("Bearer {token}"));
        assert!(headers[AUTHORIZATION].is_sensitive());
        assert!(!format!("{headers:?}").contains(&token));
        assert!(authorization("bad\r\nheader").is_err());
    }
    #[test]
    fn production_only_accepts_stable_and_test_only_test_prereleases() {
        assert!(channel_version("1.2.3", Some("production"), false));
        assert!(channel_version("1.2.3-test.1", Some("test"), false));
        for (version, channel) in [
            ("1.2.3-test.1", "production"),
            ("1.2.3", "test"),
            ("1.2.3-rc.1", "test"),
            ("1.2.3-test.1.extra", "test"),
            ("1.2.3+build", "production"),
            ("1.2.3-test.01", "test"),
        ] {
            assert!(!channel_version(version, Some(channel), false));
        }
        assert!(channel_version("1.2.3", None, true)); // Contrato legado HOR-100.
    }
    #[test]
    fn plugin_sends_bearer_on_check_and_download_and_rejects_invalid_signature() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let address = listener.local_addr().unwrap();
        let synthetic = "a".repeat(64);
        let expected = format!("authorization: Bearer {synthetic}").to_ascii_lowercase();
        let server = std::thread::spawn(move || {
            for path in ["/latest.json", "/setup.exe"] {
                let deadline = std::time::Instant::now() + Duration::from_secs(10);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(
                                std::time::Instant::now() < deadline,
                                "requisição local não recebida"
                            );
                            std::thread::sleep(Duration::from_millis(10));
                        }
                        Err(error) => panic!("{error}"),
                    }
                };
                stream
                    .set_read_timeout(Some(Duration::from_secs(5)))
                    .unwrap();
                let mut request = Vec::new();
                while !request.ends_with(b"\r\n\r\n") {
                    let mut byte = [0];
                    assert_eq!(stream.read(&mut byte).unwrap(), 1);
                    request.push(byte[0]);
                    assert!(request.len() < 8192);
                }
                let request = String::from_utf8(request).unwrap();
                assert!(request.starts_with(&format!("GET {path} ")));
                // Não imprimir o request nem a credencial se a asserção falhar.
                assert!(
                    request.to_ascii_lowercase().contains(&expected),
                    "Bearer ausente"
                );
                let body = if path == "/latest.json" {
                    json!({"version":"9.0.0","platforms":{"windows-x86_64":{"url":format!("http://{address}/setup.exe"),"signature":"invalid-fixture"}}}).to_string()
                } else {
                    "tampered-fixture".into()
                };
                write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            }
        });
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        context.config_mut().plugins.0.insert(
            "updater".into(),
            json!({
                "pubkey":"invalid-fixture", "dangerousInsecureTransportProtocol":true,
                "requireSignedVersion":true, "endpoints":[format!("http://{address}/latest.json")]
            }),
        );
        let app = tauri::test::mock_builder()
            .plugin(tauri_plugin_updater::Builder::new().build())
            .build(context)
            .unwrap();
        tauri::async_runtime::block_on(async {
            let update = app
                .updater_builder()
                .headers(authorization(&synthetic).unwrap())
                .no_proxy()
                .timeout(Duration::from_secs(5))
                .configure_client(|c| c.redirect(reqwest::redirect::Policy::none()))
                .build()
                .unwrap()
                .check()
                .await
                .unwrap()
                .unwrap();
            assert!(update.headers[AUTHORIZATION].is_sensitive());
            let error = update.download(|_, _| {}, || {}).await.unwrap_err();
            assert_eq!(download_error(&error), "signature_invalid");
        });
        server.join().unwrap();
    }
    #[test]
    fn webview_offline_mode_is_supported_by_the_nsis_template() {
        let template = include_str!("../installer.nsi");
        assert!(template.contains("\"${INSTALLWEBVIEW2MODE}\" == \"offlineInstaller\""));
        assert!(template.contains("${WEBVIEW2INSTALLERPATH}"));
        assert!(template.contains("ExecWait \"$6 ${WEBVIEW2INSTALLERARGS} /install\" $1"));
        assert!(template.contains("ReadRegStr $4 HKCU"));
        assert!(template.contains("Abort \"$(webview2AbortError)\""));
    }
    #[test]
    fn time_is_rfc3339() {
        let value = timestamp();
        assert!(value.contains('T'));
        assert!(value.ends_with('Z'));
    }
    #[test]
    fn confirmation_before_any_side_effect() {
        assert_eq!(confirm("1.1.0", "1.2.0", "1.0.0"), Err("install_failed"));
        assert_eq!(confirm("0.9.0", "0.9.0", "1.0.0"), Err("downgrade_refused"));
        assert_eq!(confirm("1.1.0", "1.1.0", "1.0.0"), Ok(()));
    }
    #[test]
    fn signature_failure_is_distinct_from_transport() {
        assert_eq!(
            download_error(&tauri_plugin_updater::Error::MissingSignedVersion),
            "signature_invalid"
        );
        assert_eq!(
            download_error(&tauri_plugin_updater::Error::SignatureUtf8(
                "fixture".into()
            )),
            "signature_invalid"
        );
    }
    #[test]
    fn malformed_http_json_is_manifest_invalid_not_network() {
        use std::io::{Read, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            let mut request = [0; 4096];
            stream.read(&mut request).unwrap();
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 1\r\nConnection: close\r\n\r\n{").unwrap();
        });
        let error = tauri::async_runtime::block_on(async {
            reqwest::Client::builder()
                .no_proxy()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap()
                .get(format!("http://{address}"))
                .send()
                .await
                .unwrap()
                .json::<Value>()
                .await
                .unwrap_err()
        });
        server.join().unwrap();
        assert!(error.is_decode());
        assert_eq!(manifest_error(&error.into()), "manifest_invalid");
    }
    #[test]
    fn restart_failure_preserves_only_safe_backup_label_and_can_recover() {
        let mut state = UpdateState::initial("1.1.0".into(), true, true);
        let label = "pre-update-00000000000040008000000000000001.zip";
        let pending = json!({"backup_path_label": label});
        for _ in 0..2 {
            apply_restart_result(&mut state, &pending, false);
            assert_eq!(state.error.as_ref().unwrap()["code"], "startup_failed");
            assert_eq!(state.backup_path_label.as_deref(), Some(label));
        }
        apply_restart_result(
            &mut state,
            &json!({"backup_path_label":"C:/private/secret"}),
            false,
        );
        assert!(state.backup_path_label.is_none());
        apply_restart_result(&mut state, &pending, true);
        assert_eq!(state.phase, "idle");
        assert!(state.error.is_none());
    }
    #[test]
    fn classifications_preserve_transport_manifest_signature_install_and_startup() {
        use tauri_plugin_updater::Error;
        assert_eq!(manifest_error(&Error::Network("offline".into())), "network");
        assert_eq!(manifest_error(&Error::ReleaseNotFound), "network");
        let invalid = serde_json::from_str::<Value>("{").unwrap_err();
        assert_eq!(
            manifest_error(&Error::Serialization(invalid)),
            "manifest_invalid"
        );
        assert_eq!(
            manifest_error(&Error::TargetNotFound("windows".into())),
            "manifest_invalid"
        );
        assert_eq!(
            manifest_error(&Error::MissingSignedVersion),
            "signature_invalid"
        );
        assert_eq!(
            download_error(&Error::Io(std::io::Error::other("disk"))),
            "install_failed"
        );
        let mut state = UpdateState::initial("1.1.0".into(), true, true);
        state.fail("startup_failed");
        assert_eq!(state.error.unwrap()["code"], "startup_failed");
        assert!(safe_backup_label(
            "pre-update-00000000-0000-4000-8000-000000000001.zip"
        ));
        assert!(!safe_backup_label("C:/Users/private/backup.zip"));
    }
    struct MockBackend {
        calls: Mutex<Vec<&'static str>>,
        activity: Mutex<std::collections::VecDeque<Result<bool, &'static str>>>,
        reserve: Result<(), &'static str>,
        backup: Result<String, &'static str>,
    }
    impl GuardBackend for MockBackend {
        async fn activity(&self) -> Result<bool, &'static str> {
            self.calls.lock().unwrap().push("activity");
            self.activity.lock().unwrap().pop_front().unwrap()
        }
        async fn reserve(&self) -> Result<(), &'static str> {
            self.calls.lock().unwrap().push("reserve");
            self.reserve
        }
        async fn backup(&self) -> Result<String, &'static str> {
            self.calls.lock().unwrap().push("backup");
            self.backup.clone()
        }
    }
    fn mock(
        activity: Vec<Result<bool, &'static str>>,
        reserve: Result<(), &'static str>,
        backup: Result<String, &'static str>,
    ) -> MockBackend {
        MockBackend {
            calls: Mutex::new(vec![]),
            activity: Mutex::new(activity.into()),
            reserve,
            backup,
        }
    }
    #[test]
    fn backend_guard_order_and_each_failure_stop_before_download() {
        tauri::async_runtime::block_on(async {
            for (mock, expected, acquired, trace) in [
                (
                    mock(vec![Err("backend_unavailable")], Ok(()), Ok("zip".into())),
                    Err("backend_unavailable"),
                    false,
                    vec!["activity"],
                ),
                (
                    mock(vec![Ok(true)], Ok(()), Ok("zip".into())),
                    Err("job_running"),
                    false,
                    vec!["activity"],
                ),
                (
                    mock(vec![Ok(false)], Err("job_running"), Ok("zip".into())),
                    Err("job_running"),
                    false,
                    vec!["activity", "reserve"],
                ),
                (
                    mock(vec![Ok(false), Ok(true)], Ok(()), Ok("zip".into())),
                    Err("job_running"),
                    true,
                    vec!["activity", "reserve", "activity"],
                ),
                (
                    mock(
                        vec![Ok(false), Err("backend_unavailable")],
                        Ok(()),
                        Ok("zip".into()),
                    ),
                    Err("backend_unavailable"),
                    true,
                    vec!["activity", "reserve", "activity"],
                ),
                (
                    mock(vec![Ok(false), Ok(false)], Ok(()), Err("backup_failed")),
                    Err("backup_failed"),
                    true,
                    vec!["activity", "reserve", "activity", "backup"],
                ),
                (
                    mock(vec![Ok(false), Ok(false)], Ok(()), Ok("zip".into())),
                    Ok("zip".into()),
                    true,
                    vec!["activity", "reserve", "activity", "backup"],
                ),
            ] {
                let mut reserved = false;
                assert_eq!(prepare(&mock, &mut reserved).await, expected);
                assert_eq!(reserved, acquired);
                assert_eq!(*mock.calls.lock().unwrap(), trace);
            }
        });
    }
    struct MockPayload {
        calls: Mutex<Vec<&'static str>>,
        fail: Option<&'static str>,
    }
    impl MockPayload {
        fn step(&self, name: &'static str) -> Result<(), &'static str> {
            self.calls.lock().unwrap().push(name);
            if self.fail == Some(name) {
                Err(if name == "download" {
                    "signature_invalid"
                } else {
                    "install_failed"
                })
            } else {
                Ok(())
            }
        }
    }
    impl PayloadBackend for MockPayload {
        async fn download(&self) -> Result<Vec<u8>, &'static str> {
            self.step("download")?;
            Ok(vec![1])
        }
        fn cache(&self, _: &[u8]) -> Result<(), &'static str> {
            self.step("cache")
        }
        async fn shutdown(&self) -> Result<(), &'static str> {
            self.step("shutdown")
        }
        fn install(&self, _: &[u8]) -> Result<(), &'static str> {
            self.step("install")
        }
    }
    #[test]
    fn signature_cache_and_shutdown_fail_closed_before_install() {
        tauri::async_runtime::block_on(async {
            let stages = ["download", "cache", "shutdown", "install"];
            for fail in [
                Some("download"),
                Some("cache"),
                Some("shutdown"),
                Some("install"),
                None,
            ] {
                let mock = MockPayload {
                    calls: Mutex::new(vec![]),
                    fail,
                };
                let result = finish_update(&mock).await;
                assert_eq!(result.is_ok(), fail.is_none());
                let length = fail.map_or(4, |name| {
                    stages.iter().position(|s| *s == name).unwrap() + 1
                });
                assert_eq!(*mock.calls.lock().unwrap(), stages[..length]);
            }
        });
    }
}
