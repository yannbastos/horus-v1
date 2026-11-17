use base64::{engine::general_purpose::STANDARD, Engine};
use minisign_verify::{PublicKey, Signature};
use std::{env, fs};

fn verify() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.len() != 5 {
        return Err("Uso: verifier <pubkey-file> <installer> <sig-file> <version>".into());
    }
    // Tauri codifica em base64 o documento minisign completo, tanto chave quanto assinatura.
    let key = String::from_utf8(STANDARD.decode(fs::read_to_string(&args[1])?.trim())?)?;
    let sig = String::from_utf8(STANDARD.decode(fs::read_to_string(&args[3])?.trim())?)?;
    let public = PublicKey::decode(&key)?;
    let signature = Signature::decode(&sig)?;
    public.verify(&fs::read(&args[2])?, &signature, false)?;
    let signed_version = signature
        .trusted_comment()
        .split_whitespace()
        .find_map(|s| s.strip_prefix("version:"));
    if signed_version != Some(args[4].as_str()) {
        return Err("Assinatura não vinculada à versão anunciada".into());
    }
    Ok(())
}

fn main() {
    if verify().is_err() {
        eprintln!("Verificação minisign/versão falhou (detalhes omitidos).");
        std::process::exit(1);
    }
}
