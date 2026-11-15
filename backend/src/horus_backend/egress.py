"""Gateway único de saída HTTP do backend (HOR-111): negar por padrão, destinos só por configuração explícita.

Todo destino de rede do backend passa por aqui, em duas formas:
- `EgressGateway.request(...)` executa a requisição: valida URL/método/cabeçalhos, procura regra na allowlist, recusa
  carga com caminho local ou credencial, resolve o DNS **uma vez**, recusa IP privado/loopback/link-local (salvo regra
  explícita de loopback), conecta ao IP já validado (sem nova resolução: fecha o DNS rebinding), segue redirects só se o
  novo destino também for permitido (e sem reenviar `Authorization` a outra origem), limita o tamanho da resposta.
- `EgressGateway.authorize(...)` só decide e registra (para clientes que já têm transporte próprio, como o loopback do
  Hermes, o yt-dlp e o Radar). Não resolve DNS: hostnames são avaliados pelo nome; IP literal é avaliado pelo endereço.
Toda decisão vira linha em `transmissions` (kind `submit`/`gate_refusal`, stage `egress:<finalidade>`): só destino
(host:porta), tamanho e SHA-256 da carga e um código curto; nunca conteúdo. Regras extras: `HORUS_EGRESS_ALLOW`
(`finalidade=https://host[:porta][,auth][,loopback][,methods=GET+POST]`, separadas por `;`).
Limites: o yt-dlp e o download de modelos (Hugging Face) rodam em subprocessos que fazem a própria rede; aqui só se
autoriza o destino inicial (ver docs/politica-destinos-e-ameacas.md).
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from .hermes_contract.models import _PRIVATE_TEXT

MAX_REDIRECTS = 3
DEFAULT_MAX_BYTES = 256 * 1024
FORBIDDEN_HEADERS = {"cookie", "proxy-authorization", "proxy-connection", "host", "content-length", "transfer-encoding"}
_CONTROL = re.compile(r"[\x00-\x20\x7f]")
_HEADER_VALUE = re.compile(r"^[\x20-\x7e]*$")


class EgressBlocked(Exception):
    """Saída recusada; `code` é estável e seguro para registrar e devolver."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class Rule:
    purpose: str
    scheme: str
    host: str
    port: int | None = None           # None = porta padrão do esquema
    allow_loopback: bool = False      # destino pode ser 127.0.0.1/::1 (só Hermes local)
    allow_auth: bool = False          # pode enviar `Authorization`
    methods: frozenset = field(default_factory=lambda: frozenset({"GET"}))

    @property
    def effective_port(self) -> int:
        return self.port or (443 if self.scheme == "https" else 80)


# Destinos que o produto usa hoje, listados de forma explícita; qualquer outro exige configuração.
DEFAULT_RULES = (
    *(Rule("youtube_download", "https", host) for host in ("www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be")),
    Rule("radar_youtube", "https", "www.googleapis.com"),
)


def parse_rules(text: str) -> tuple[Rule, ...]:
    rules = []
    for item in filter(None, (part.strip() for part in (text or "").split(";"))):
        purpose, _, rest = item.partition("=")
        url, *flags = [p.strip() for p in rest.split(",")]
        parts = urlsplit(url)
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,40}", purpose) or parts.scheme not in {"http", "https"} or not parts.hostname \
                or parts.username or parts.password or parts.path not in {"", "/"} or parts.query:
            raise ValueError(f"Regra de saída inválida: {purpose!r}")
        methods, auth, loop = {"GET"}, False, False
        for flag in flags:
            if flag == "auth":
                auth = True
            elif flag == "loopback":
                loop = True
            elif flag.startswith("methods="):
                methods = set(flag[8:].upper().split("+")) & {"GET", "POST"}
            else:
                raise ValueError(f"Opção de regra desconhecida: {flag!r}")
        if parts.scheme == "http" and not loop:
            raise ValueError("http só é aceito para destino loopback explícito.")
        rules.append(Rule(purpose, parts.scheme, parts.hostname.lower(), parts.port, loop, auth, frozenset(methods)))
    return tuple(rules)


def _norm_ip(value):
    ip = ipaddress.ip_address(value)
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def _is_public(ip) -> bool:
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_unspecified)


@dataclass
class EgressResponse:
    status: int
    headers: dict
    body: bytes
    destination: str


class EgressGateway:
    def __init__(self, rules=DEFAULT_RULES, *, audit=None, resolver=None, transport=None, hermes_loopback=True):
        self.rules = tuple(rules)
        self.audit = audit
        self.resolver = resolver or (lambda host, port: [i[4][0] for i in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)])
        self.transport = transport or _http_transport
        self.hermes_loopback = hermes_loopback

    # --- política ------------------------------------------------------------------------------------------------

    def _rule(self, purpose, scheme, host, port):
        if purpose == "hermes" and self.hermes_loopback:
            # O endereço vem do arquivo de conexão explícito do Hermes: somente http em loopback com porta e token.
            if scheme == "http" and host in {"127.0.0.1", "::1"} and port:
                return Rule("hermes", "http", host, port, True, True, frozenset({"GET", "POST"}))
        for rule in self.rules:
            if rule.purpose == purpose and rule.scheme == scheme and rule.host == host \
                    and rule.effective_port == (port or (443 if scheme == "https" else 80)):
                return rule
        return None

    def _check(self, purpose, method, url, headers, body, *, resolve):
        if not isinstance(url, str) or _CONTROL.search(url) or len(url) > 4096:
            raise EgressBlocked("url_invalid", "URL inválida.")
        parts = urlsplit(url)
        try:
            host, port = (parts.hostname or "").lower(), parts.port
        except ValueError:
            raise EgressBlocked("url_invalid", "Porta inválida.") from None
        if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password or parts.fragment:
            raise EgressBlocked("url_invalid", "Use http(s) sem credenciais na URL.")
        rule = self._rule(purpose, parts.scheme, host, port)
        if rule is None:
            raise EgressBlocked("destination_not_allowed", "Destino fora da allowlist de saída.")
        if method.upper() not in rule.methods:
            raise EgressBlocked("method_not_allowed", "Método não permitido para este destino.")
        for name, value in (headers or {}).items():
            lowered = str(name).lower()
            if lowered in FORBIDDEN_HEADERS or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(name)) \
                    or not _HEADER_VALUE.fullmatch(str(value)):
                raise EgressBlocked("header_not_allowed", "Cabeçalho não permitido.")
            if lowered == "authorization" and not rule.allow_auth:
                raise EgressBlocked("auth_not_allowed", "Este destino não recebe credenciais.")
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "")
        if _PRIVATE_TEXT.search(text) or _PRIVATE_TEXT.search(parts.query or ""):
            raise EgressBlocked("payload_private", "A carga contém caminho local ou credencial.")
        ips = self._addresses(host, rule.effective_port, rule, resolve)
        return rule, host, rule.effective_port, ips

    def _addresses(self, host, port, rule, resolve):
        try:
            literal = [_norm_ip(host)]
        except ValueError:
            literal = None
        if literal is None:
            if (host == "localhost" or host.endswith(".localhost")) and not rule.allow_loopback:
                raise EgressBlocked("private_address", "Destino local não permitido.")
            if not resolve:
                return []
            try:
                literal = [_norm_ip(a) for a in self.resolver(host, port)]
            except (OSError, ValueError):
                raise EgressBlocked("dns_failure", "Não foi possível resolver o destino.") from None
            if not literal:
                raise EgressBlocked("dns_failure", "Não foi possível resolver o destino.")
        for ip in literal:
            if _is_public(ip):
                continue
            if ip.is_loopback and rule.allow_loopback:
                continue
            raise EgressBlocked("private_address", "Endereço privado, local ou reservado não permitido.")
        return [str(ip) for ip in literal]

    # --- auditoria -----------------------------------------------------------------------------------------------

    def _record(self, purpose, result, destination, body, code=None, kind=None):
        if self.audit is None:
            return
        text = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else body
        self.audit.best_effort(kind or ("gate_refusal" if result == "policy_violation" else "submit"), "egress:" + purpose,
                               result, destination=destination, provider="egress", envelope=text if text else None,
                               detail=code)

    @staticmethod
    def _destination(url):
        try:
            parts = urlsplit(url)
            return f"{parts.scheme}://{(parts.hostname or '?')[:100]}:{parts.port or (443 if parts.scheme == 'https' else 80)}"
        except ValueError:
            return "invalid"

    # --- API -----------------------------------------------------------------------------------------------------

    def authorize(self, purpose, method, url, *, headers=None, body=None):
        """Decide e registra; levanta `EgressBlocked`. Não faz E/S de rede nem resolve DNS."""
        destination = self._destination(url)
        try:
            self._check(purpose, method, url, headers, body, resolve=False)
        except EgressBlocked as error:
            self._record(purpose, "policy_violation", destination, body, error.code)
            raise
        if purpose != "hermes":  # o fluxo do Hermes já registra submit/outcome próprios (HOR-115); recusas sempre entram
            self._record(purpose, "attempted", destination, body, "authorized")

    def request(self, purpose, method, url, *, headers=None, body=None, timeout=15, max_bytes=DEFAULT_MAX_BYTES) -> EgressResponse:
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        headers = dict(headers or {})
        origin = None
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            destination = self._destination(current)
            try:
                rule, host, port, ips = self._check(purpose, method, current, headers, body, resolve=True)
            except EgressBlocked as error:
                self._record(purpose, "policy_violation", destination, body, error.code)
                raise
            parts = urlsplit(current)
            origin = origin or (parts.scheme, host, port)
            try:
                response = self.transport(ips[0], port, parts.scheme, host, method.upper(),
                                          (parts.path or "/") + (("?" + parts.query) if parts.query else ""),
                                          headers, body, timeout, max_bytes)
            except EgressBlocked as error:
                self._record(purpose, "policy_violation", destination, body, error.code)
                raise
            except (OSError, http.client.HTTPException):
                self._record(purpose, "error", destination, body, "transport_error", kind="outcome")
                raise EgressBlocked("transport_error", "Falha de transporte.") from None
            status, response_headers, data = response
            if status in {301, 302, 303, 307, 308}:
                location = {k.lower(): v for k, v in response_headers.items()}.get("location", "")
                target = urljoin(current, location) if location else ""
                if method.upper() != "GET" or not target:
                    self._record(purpose, "policy_violation", destination, body, "redirect_blocked")
                    raise EgressBlocked("redirect_blocked", "Redirecionamento recusado.")
                new_parts = urlsplit(target)
                if (new_parts.scheme, (new_parts.hostname or "").lower(), new_parts.port or (443 if new_parts.scheme == "https" else 80)) != \
                        (origin[0], origin[1], origin[2]):
                    headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}  # nunca para outra origem
                current, body = target, None
                continue
            if len(data) > max_bytes:
                self._record(purpose, "policy_violation", destination, body, "response_too_large")
                raise EgressBlocked("response_too_large", "Resposta acima do limite.")
            self._record(purpose, "sent", destination, body, "http_%d" % status, kind="submit")
            return EgressResponse(status, dict(response_headers), data, destination)
        self._record(purpose, "policy_violation", self._destination(current), body, "too_many_redirects")
        raise EgressBlocked("too_many_redirects", "Redirecionamentos demais.")


def _http_transport(ip, port, scheme, host, method, target, headers, body, timeout, max_bytes):
    """Conecta ao IP **já validado**; o nome só vai em `Host`/SNI. Sem proxy de ambiente."""
    sock = socket.create_connection((ip, port), timeout=timeout)
    try:
        if scheme == "https":
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.sock = sock
        connection.request(method, target, body=body, headers={**headers, "Host": host if port in {80, 443} else f"{host}:{port}"})
        response = connection.getresponse()
        data = response.read(max_bytes + 1)
        return response.status, {k: v for k, v in response.getheaders()}, data
    finally:
        sock.close()


_gateway: EgressGateway | None = None


def configure(gateway: EgressGateway | None) -> None:
    global _gateway
    _gateway = gateway


def gateway() -> EgressGateway:
    global _gateway
    if _gateway is None:
        _gateway = EgressGateway(DEFAULT_RULES + parse_rules(os.environ.get("HORUS_EGRESS_ALLOW", "")))
    return _gateway


def build_gateway(audit=None) -> EgressGateway:
    return EgressGateway(DEFAULT_RULES + parse_rules(os.environ.get("HORUS_EGRESS_ALLOW", "")), audit=audit)
